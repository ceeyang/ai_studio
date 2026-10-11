"""环境检查：ComfyUI、各引擎的节点与模型、网页出图浏览器与登录、LLM、ffmpeg、磁盘、内存。
只报告，不改状态（不会为了检查去启动浏览器）。"""
from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import httpx
import psutil

from backend.config import Config
from backend.engines.browser.errors import EXT_INSTALL_HINT, EXT_LOGIN_HINT, LOGIN_HINT
from backend.engines.comfyui.errors import ComfyError
from backend.engines.registry import runtime_of


@dataclass
class Check:
    group: str
    name: str
    status: Literal["ok", "warn", "fail"]
    detail: str = ""


def _http_get(url: str) -> Any:
    r = httpx.get(url, timeout=3, trust_env=False)
    r.raise_for_status()
    return r.json()


def _model_index(models_dir: Path | None) -> set[str] | None:
    if not models_dir or not Path(models_dir).is_dir():
        return None
    return {p.name.lower() for p in Path(models_dir).rglob("*") if p.is_file()}


def _basename(f: str) -> str:
    return f.replace("\\", "/").rsplit("/", 1)[-1].lower()


def check_browser(engines, browser) -> list[Check]:
    """网页图像引擎：浏览器程序、调试端口、各网站是否登录（按 cookie，不开页面、不出图）。"""
    used = {e.site: e for e in engines.all().values() if runtime_of(e) == "browser"}
    if not used or browser is None:
        return []
    if getattr(browser, "transport", "cdp") == "extension":
        return _check_extension(used, browser)
    out: list[Check] = []
    sess = browser.session
    exe = sess.executable()
    out.append(Check("浏览器", "程序", "ok" if exe else "fail",
                     str(exe) if exe else "找不到 Chrome 或 Edge：装一个，或在 config.yaml 的 browser.executable 写路径"))
    v = sess.version()
    if v is not None:
        out.append(Check("浏览器", "调试端口", "ok",
                         f"{sess.endpoint} · {v.get('Browser', '?')} · 配置目录 {sess.cfg.user_data_dir}"))
    elif sess.port_taken():
        out.append(Check("浏览器", "调试端口", "fail",
                         f"端口 {sess.cfg.cdp_port} 被别的程序占用，在 config.yaml 的 browser.cdp_port 换一个"))
    else:
        out.append(Check("浏览器", "调试端口", "ok" if exe else "fail",
                         f"{sess.endpoint} 没在运行，出图或点“打开浏览器登录”时自动启动"
                         f"（配置目录 {sess.cfg.user_data_dir}）"))
    status: dict[str, bool] | None = None
    err = ""
    if v is not None:
        try:
            status = browser.login_status()
        except Exception as e:
            err = f"读登录状态失败：{e}"
    else:
        err = "浏览器没在运行，查不了登录状态"
    for key, eng in sorted(used.items()):
        site = browser.sites.get(key)
        label = site.label if site else key
        name = f"{label} 登录（{eng.name}）"
        if status is None:
            out.append(Check("浏览器", name, "warn", f"{err}。{LOGIN_HINT}"))
        elif status.get(key):
            out.append(Check("浏览器", name, "ok", f"已登录 {site.host if site else key}"))
        else:
            out.append(Check("浏览器", name, "warn",
                             f"没登录 {site.host if site else key}，{eng.name} 出图会失败。{LOGIN_HINT}"))
    return out


def _check_extension(used: dict, browser) -> list[Check]:
    """transport=extension：扩展连没连上、各网站在那个浏览器里有没有登录（扩展报的，不开页面、不出图）。"""
    info = browser.hub.info()
    out: list[Check] = []
    if not info.get("connected"):
        out.append(Check("浏览器", "扩展连接", "warn",
                         f"浏览器扩展没有连上，gpt_image / gemini_image 出图会失败。{EXT_INSTALL_HINT}"))
    else:
        out.append(Check("浏览器", "扩展连接", "ok",
                         f"已连上 · 扩展 v{info.get('version') or '?'} · {info.get('browser') or '浏览器'}"
                         f" · id {info.get('ext_id') or '?'}"))
    st = browser.hub.site_status()
    for key, eng in sorted(used.items()):
        site = browser.sites.get(key)
        label = site.label if site else key
        host = site.host if site else key
        name = f"{label} 登录（{eng.name}）"
        s = st.get(key)
        if not info.get("connected"):
            out.append(Check("浏览器", name, "warn", f"扩展没连上，查不了登录状态。{EXT_LOGIN_HINT}"))
        elif s is None:
            out.append(Check("浏览器", name, "warn", f"扩展还没报 {host} 的状态，稍后重新检查"))
        else:
            tabs = s.get("tabs")
            tab_text = f"开着 {tabs} 个标签页" if tabs else "没有开着的标签页（出图时扩展会在后台新开一个）"
            if s.get("logged_in"):
                out.append(Check("浏览器", name, "ok", f"已登录 {host} · {tab_text}"))
            else:
                out.append(Check("浏览器", name, "warn",
                                 f"没登录 {host}，{eng.name} 出图会失败。{EXT_LOGIN_HINT} · {tab_text}"))
    return out


def check_runninghub(cfg: Config, engines) -> list[Check]:
    """RunningHub 云端引擎：有没有 API key、配了哪些引擎（不联网、不扣费）。没配 runninghub 就不报。"""
    rh = cfg.runninghub
    used = {n: e for n, e in engines.all().items() if runtime_of(e) == "runninghub"}
    broken = dict(getattr(engines, "load_errors", {}) or {})
    if rh is None or not (used or broken):
        return []
    out: list[Check] = []
    if rh.api_key():
        out.append(Check("RunningHub", "API key", "ok", f"已从环境变量 {rh.api_key_env} 读到 · {rh.base_url}"))
    else:
        out.append(Check("RunningHub", "API key", "warn",
                         f"没有设置 RunningHub API key（环境变量 {rh.api_key_env}），"
                         f"{'、'.join(sorted(used)) or '云端引擎'} 用不了：在 .env 里写上 {rh.api_key_env}=你的 key 后重启 studio"))
    for name, e in sorted(used.items()):
        target = f"工作流 {e.cfg.workflow_id}" if e.cfg.workflow_id else f"AI 应用 {e.cfg.webapp_id}"
        kind = "图像" if e.cfg.kind == "image" else "视频"
        out.append(Check("RunningHub", name, "ok", f"{e.label} · {kind} · {target}"))
    for name, err in sorted(broken.items()):
        out.append(Check("RunningHub", name, "fail", f"配置有误，没有加载：{err}"))
    return out


def run_doctor(cfg: Config, engines, comfy, ffmpeg, *, browser=None,
               http_get: Callable[[str], Any] = _http_get,
               which: Callable[[str], str | None] = shutil.which) -> list[Check]:
    out: list[Check] = []

    info = None
    if comfy is None:
        out.append(Check("ComfyUI", "服务", "fail", "未配置"))
    else:
        try:
            st = comfy.system_stats(tries=1, timeout=3)
            dev = (st.get("devices") or [{}])[0]
            out.append(Check("ComfyUI", "服务", "ok",
                             f"{cfg.comfyui.url} · {dev.get('name', '?')} · 显存空闲 "
                             f"{dev.get('vram_free', 0) / 2**30:.1f}/{dev.get('vram_total', 0) / 2**30:.1f} GB"))
        except ComfyError as e:
            out.append(Check("ComfyUI", "服务", "fail", str(e)))
        else:
            try:
                info = comfy.object_info(tries=1, timeout=15)
            except ComfyError as e:
                out.append(Check("ComfyUI", "节点信息", "fail", str(e)))

    index = _model_index(cfg.comfyui.models_dir)
    if index is None:
        out.append(Check("引擎", "模型目录", "warn",
                         "没配置 comfyui.models_dir 或目录不存在，跳过模型文件检查"))
    for name, eng in sorted(engines.all().items()):
        if runtime_of(eng) != "comfyui":
            continue
        if info is not None:
            types = eng.tpl.class_types()
            missing = sorted(types - set(info))
            out.append(Check("引擎", f"{name} 节点", "fail" if missing else "ok",
                             "缺少节点：" + "、".join(missing) if missing else f"{len(types)} 个节点都在"))
        if index is not None:
            files = eng.tpl.model_files(eng.overrides)
            miss = [f for f in files if _basename(f) not in index]
            out.append(Check("引擎", f"{name} 模型", "fail" if miss else "ok",
                             "缺少：" + "、".join(miss) if miss else f"{len(files)} 个模型文件都在"))

    out += check_browser(engines, browser)
    out += check_runninghub(cfg, engines)

    for p in cfg.llm.providers:
        if p.kind == "openai":
            try:
                d = http_get(p.base_url.rstrip("/") + "/models")
                n = len(d.get("data") or [])
                out.append(Check("LLM", p.id, "ok", f"{p.base_url} · {n} 个模型"))
            except Exception as e:
                out.append(Check("LLM", p.id, "warn",
                                 f"连不上 {p.base_url}（{type(e).__name__}）。本机 LM Studio 平时不开，用到前先启动"))
        elif p.kind == "claude":
            exe = which("claude")
            out.append(Check("LLM", p.id, "ok" if exe else "fail", exe or "找不到 claude 命令"))
        else:
            out.append(Check("LLM", p.id, "ok", "手动粘贴结果"))

    try:
        v = ffmpeg.version()
        first = (v.splitlines() or ["ffmpeg"])[0]
        if "--enable-libass" in v:
            out.append(Check("工具", "ffmpeg", "ok", first))
        else:
            out.append(Check("工具", "ffmpeg", "warn", first + "（没有 libass，烧字幕会失败）"))
    except Exception as e:
        out.append(Check("工具", "ffmpeg", "fail", str(e)))

    probe = Path(cfg.data_dir).resolve()
    while not probe.exists() and probe != probe.parent:  # 数据目录还没建时看它所在的盘
        probe = probe.parent
    free = shutil.disk_usage(probe).free / 2**30
    out.append(Check("系统", "磁盘", "ok" if free >= 50 else "warn",
                     f"{cfg.data_dir} 所在盘剩余 {free:.0f} GB"))
    mem = psutil.virtual_memory()
    avail = mem.available / 2**30
    out.append(Check("系统", "内存", "ok" if avail >= 8 else "warn",
                     f"可用 {avail:.1f} / {mem.total / 2**30:.0f} GB"))
    return out
