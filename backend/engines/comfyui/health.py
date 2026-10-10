"""ComfyUI 连没连上：GET /system_stats，1 秒超时；连上的结果缓存 15 秒，连不上的只缓存 2 秒
（用户刚把 ComfyUI 开起来，马上就能提交）。

提交任务前的校验和 /api/system/health 都用它。测试里 monkeypatch check_comfy。
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from backend.engines.comfyui.errors import ComfyError

TIMEOUT = 1.0
TTL = 15.0
FAIL_TTL = 2.0


@dataclass(frozen=True)
class ComfyHealth:
    ok: bool
    message: str      # 一句短中文，如「连不上 127.0.0.1:8188（超时）」
    detail: str = ""  # 原始错误文本（英文异常等），界面放在悬停提示 / 调试信息里


_cache: dict[str, tuple[float, ComfyHealth]] = {}
_lock = threading.Lock()


def reset_cache() -> None:
    with _lock:
        _cache.clear()


def comfy_url(client) -> str:
    return getattr(client, "base_url", "") or "未配置地址"


def _host(url: str) -> str:
    """「http://127.0.0.1:8188/」→「127.0.0.1:8188」；解析不出主机时原样返回。"""
    parts = urlsplit(url)
    return parts.netloc or url


_BAD_ADDRESS = ("getaddrinfo", "name or service not known", "nodename nor servname",
                "no address associated", "name resolution")


def _reason(e: BaseException) -> str:
    """异常归成一句中文原因：超时 / 拒绝连接 / 地址不对 / ComfyUI 出错 HTTP 5xx / 返回的不是 ComfyUI 的数据。"""
    if isinstance(e, ComfyError) and e.status is not None:
        return f"ComfyUI 出错 HTTP {e.status}"
    cause = e.__cause__ if isinstance(e, ComfyError) and e.__cause__ is not None else e
    if isinstance(cause, httpx.TimeoutException):
        return "超时"
    if isinstance(cause, (httpx.UnsupportedProtocol, httpx.InvalidURL)):
        return "地址不对"
    if isinstance(cause, httpx.ConnectError):
        text = str(cause).lower()
        return "地址不对" if any(k in text for k in _BAD_ADDRESS) else "拒绝连接"
    if isinstance(cause, ValueError):   # 返回的不是 JSON
        return "返回的不是 ComfyUI 的数据"
    return "连接出错"


def _detail(e: BaseException) -> str:
    text = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
    cause = e.__cause__
    if cause is not None and str(cause) not in text:
        text += f"（{type(cause).__name__}: {cause}）"
    return text


def check_comfy(client, *, clock: Callable[[], float] = time.monotonic) -> ComfyHealth:
    if client is None:
        return ComfyHealth(False, "ComfyUI 未配置")
    url = comfy_url(client)
    now = clock()
    with _lock:
        hit = _cache.get(url)
        if hit is not None and now - hit[0] < (TTL if hit[1].ok else FAIL_TTL):
            return hit[1]
    try:
        client.system_stats(tries=1, timeout=TIMEOUT)
        h = ComfyHealth(True, f"已连上 {url}")
    except Exception as e:   # 连不上、超时、返回不是 JSON：都算连不上
        h = ComfyHealth(False, f"连不上 {_host(url)}（{_reason(e)}）", _detail(e))
    with _lock:
        _cache[url] = (now, h)
    return h
