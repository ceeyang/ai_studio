"""浏览器扩展桥的 WebSocket：/api/browser/ext（协议见 backend/engines/browser/extension.py）。

只有这个端点接受浏览器扩展的来源（chrome-extension://<id>，Edge 也是这个；少数浏览器报 extension://），
本机页面、别的网站、不带 Origin 的客户端一律在握手时拒绝。Host 检查（TrustedHostMiddleware）照旧，
扩展连的是 ws://127.0.0.1:8700。config.yaml 的 browser.extension_id 写了就只认这一个扩展。
"""
from __future__ import annotations

import re

from fastapi import APIRouter, WebSocket

router = APIRouter(prefix="/api/browser", tags=["browser"])
POLICY_VIOLATION = 1008
EXT_ORIGIN = re.compile(r"^(chrome-extension|extension)://([a-z0-9]{1,64})/?$")


def extension_origin_ok(origin: str | None, pinned_id: str | None = None) -> bool:
    if not origin:
        return False
    m = EXT_ORIGIN.match(origin)
    if not m:
        return False
    return not pinned_id or m.group(2) == pinned_id.strip().lower()


@router.websocket("/ext")
async def extension_bridge(sock: WebSocket):
    origin = sock.headers.get("origin")
    cfg = sock.app.state.cfg
    if not extension_origin_ok(origin, cfg.browser.extension_id):
        await sock.close(code=POLICY_VIOLATION)
        return
    browser = sock.app.state.services.browser
    if browser is None:
        await sock.close(code=POLICY_VIOLATION)
        return
    await sock.accept()
    await browser.hub.serve(sock, origin)
