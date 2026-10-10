"""ComfyUI 连没连上：GET /system_stats，1 秒超时，结果缓存 15 秒。

提交任务前的校验和 /api/system/health 都用它。测试里 monkeypatch check_comfy。
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

TIMEOUT = 1.0
TTL = 15.0


@dataclass(frozen=True)
class ComfyHealth:
    ok: bool
    message: str


_cache: dict[str, tuple[float, ComfyHealth]] = {}
_lock = threading.Lock()


def reset_cache() -> None:
    with _lock:
        _cache.clear()


def comfy_url(client) -> str:
    return getattr(client, "base_url", "") or "未配置地址"


def check_comfy(client, *, clock: Callable[[], float] = time.monotonic) -> ComfyHealth:
    if client is None:
        return ComfyHealth(False, "ComfyUI 未配置")
    url = comfy_url(client)
    now = clock()
    with _lock:
        hit = _cache.get(url)
        if hit is not None and now - hit[0] < TTL:
            return hit[1]
    try:
        client.system_stats(tries=1, timeout=TIMEOUT)
        h = ComfyHealth(True, f"已连上 {url}")
    except Exception as e:   # 连不上、超时、返回不是 JSON：都算连不上
        h = ComfyHealth(False, f"连不上 {url}：{str(e).splitlines()[0] if str(e) else type(e).__name__}")
    with _lock:
        _cache[url] = (now, h)
    return h
