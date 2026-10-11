"""健康检查、引擎列表、配置、网页出图浏览器（打开登录、状态）。"""
from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Request

from backend.app.deps import services
from backend.doctor import run_doctor
from backend.engines.comfyui import health as comfy_health
from backend.queue.errors import JobError
from backend.engines.registry import runtime_of

router = APIRouter(prefix="/api", tags=["system"])
VERSION = "0.1.0"


@router.get("/health")
def health():
    return {"ok": True, "version": VERSION}


def _browser_ext(browser) -> dict:
    """网页出图的浏览器扩展连没连上（只读扩展最近一次的状态，不开浏览器）。"""
    if browser is None:
        return {"ok": False, "message": "网页出图的浏览器没有配置"}
    if browser.transport != "extension":
        return {"ok": True, "message": "网页出图用 studio 自己开的浏览器（cdp），不需要扩展"}
    info = browser.hub.info()
    if info.get("connected"):
        return {"ok": True, "message": f"浏览器扩展已连上 · v{info.get('version') or '?'}"
                                       f" · {info.get('browser') or '浏览器'}"}
    return {"ok": False, "message": "浏览器扩展没有连上，网页出图、出视频（ChatGPT / Gemini / Muse）会失败；到设置页看安装步骤"}


@router.get("/system/health")
def system_health(request: Request):
    """顶栏状态灯用：ComfyUI 连没连上（1 秒超时、缓存 15 秒）、浏览器扩展连没连上。"""
    sv = services(request)
    h = comfy_health.check_comfy(sv.comfy_client)
    comfy = {"ok": h.ok, "message": h.message}
    if h.detail:
        comfy["detail"] = h.detail
    return {"comfyui": comfy, "browser_ext": _browser_ext(sv.browser)}


def engine_note_fn(sv):
    """引擎下拉框用：返回 note(engine) → 现在用不了的原因（None = 能用）。
    API / RunningHub 看自己的 preflight（没配 Key）；本机 ComfyUI 引擎看 ComfyUI 连没连上；
    网页出图引擎看浏览器扩展连没连上。ComfyUI 和扩展的状态一次请求只查一次。"""
    cache: dict[str, str | None] = {}

    def comfy() -> str | None:
        if "comfy" not in cache:
            h = comfy_health.check_comfy(sv.comfy_client)
            cache["comfy"] = None if h.ok else (h.message if h.message.startswith("ComfyUI")
                                                else f"ComfyUI {h.message}")
        return cache["comfy"]

    def browser() -> str | None:
        if "browser" not in cache:
            b = _browser_ext(sv.browser)
            cache["browser"] = None if b["ok"] else b["message"]
        return cache["browser"]

    def note(e) -> str | None:
        pre = getattr(e, "preflight", None)
        msg = pre() if callable(pre) else None
        if msg:
            return msg
        rt = runtime_of(e)
        if rt == "comfyui":
            return comfy()
        if rt == "browser":
            return browser()
        return None

    return note


@router.get("/engines")
def engines(request: Request):
    sv = services(request)
    reg = sv.engines
    engine_note = engine_note_fn(sv)

    def row(name):
        e = reg.get(name)
        note = engine_note(e)
        out = {"name": name, "label": getattr(e, "label", None) or name, "family": e.family,
               "runtime": runtime_of(e), "caps": asdict(e.caps), "ready": note is None, "note": note}
        if runtime_of(e) == "api":
            out["service"] = e.service
            out["model"] = e.model()
        return out

    return {"image": [row(n) for n in reg.names("image")],
            "video": [row(n) for n in reg.names("video")]}


@router.get("/config")
def config(request: Request):
    # 配置里只有环境变量名，没有密钥值
    return request.app.state.cfg.model_dump(mode="json")


@router.get("/doctor")
def doctor(request: Request):
    sv = services(request)
    return [asdict(c) for c in run_doctor(sv.cfg, sv.engines, sv.comfy_client, sv.ffmpeg,
                                          browser=sv.browser, api_keys=getattr(sv, "api_keys", None),
                                          settings=getattr(sv, "settings", None))]


@router.post("/browser/login")
def browser_login(request: Request):
    """让用户登录 ChatGPT 和 Gemini。transport=extension：让扩展在用户自己的浏览器里打开两个网站
    （扩展没连上时报错并给安装步骤）；cdp：打开 studio 管的浏览器（没在运行就启动，在运行就新开标签页）。"""
    sv = services(request)
    if sv.browser is None:
        raise JobError("网页出图的浏览器没有配置")
    return sv.browser.open_login()


@router.get("/browser/status")
def browser_status(request: Request):
    """extension：扩展是否连上、各网站开着几个标签页、是否登录；cdp：浏览器是否在运行、各网站是否登录
    （按 cookie）。都不会启动浏览器。"""
    sv = services(request)
    if sv.browser is None:
        return {"transport": None, "running": False, "connected": False, "logged_in": {}}
    if sv.browser.transport == "extension":
        sv.browser.hub.request_status()   # 让扩展马上再报一次（下一次刷新就是新的）
    return sv.browser.status()


@router.get("/browser/conversations")
def browser_conversations(request: Request, scope_kind: str, scope_id: str):
    """某个角色 / 镜头 / 场景 / 道具（scope_kind=character|shot|location|prop）在各网站上记下的网页会话：
    重新生成时会接着这条对话发。界面上显示「在网页里打开这条会话」的链接用。"""
    from backend.engines.browser.conversations import IMAGE_KINDS, MAX_TURNS, TEXT_KINDS

    if scope_kind not in (*IMAGE_KINDS, *TEXT_KINDS):
        raise JobError(f"没有这种作用域：{scope_kind}，可选：{', '.join((*IMAGE_KINDS, *TEXT_KINDS))}")
    sv = services(request)
    browser = sv.browser
    book = getattr(browser, "conversations", None) if browser is not None else None
    sites = getattr(browser, "sites", {}) if browser is not None else {}
    items = book.list(scope_kind, scope_id) if book is not None else []
    support = browser.conversation_support() if browser is not None else {"supported": False, "hint": None}
    return {"reuse_enabled": book.enabled() if book is not None else False,
            "supported": bool(support.get("supported")), "hint": support.get("hint"),
            "max_turns": getattr(book, "max_turns", MAX_TURNS),
            "items": [{"site": r["site"], "label": sites[r["site"]].label if r["site"] in sites else r["site"],
                       "url": r["url"], "turns": r["turns"], "updated_at": r["updated_at"]} for r in items]}
