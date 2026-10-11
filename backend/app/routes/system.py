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
    return {"ok": False, "message": "浏览器扩展没有连上，ChatGPT / Gemini 出图会失败；到设置页看安装步骤"}


@router.get("/system/health")
def system_health(request: Request):
    """顶栏状态灯用：ComfyUI 连没连上（1 秒超时、缓存 15 秒）、浏览器扩展连没连上。"""
    sv = services(request)
    h = comfy_health.check_comfy(sv.comfy_client)
    comfy = {"ok": h.ok, "message": h.message}
    if h.detail:
        comfy["detail"] = h.detail
    return {"comfyui": comfy, "browser_ext": _browser_ext(sv.browser)}


@router.get("/engines")
def engines(request: Request):
    reg = services(request).engines

    def row(name):
        e = reg.get(name)
        pre = getattr(e, "preflight", None)
        note = pre() if callable(pre) else None
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
