"""WebSocket：推送任务状态、日志、进度、队列状态。"""
from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool

from backend.app.deps import is_local_origin

router = APIRouter(prefix="/api", tags=["ws"])
POLICY_VIOLATION = 1008


@router.websocket("/ws")
async def events(sock: WebSocket):
    # 浏览器里的 WebSocket 不受同源策略限制，别的网站的页面也能连上来看任务日志和提示词；
    # 所以带了 Origin 且不是本机页面的一律在握手时拒绝。CLI 等非浏览器客户端不带 Origin，放行。
    origin = sock.headers.get("origin")
    if origin is not None and not is_local_origin(origin):
        await sock.close(code=POLICY_VIOLATION)
        return
    await sock.accept()
    bus = sock.app.state.bus
    q = bus.subscribe()
    try:
        state = await run_in_threadpool(sock.app.state.manager.queue_state)
        await sock.send_json({"type": "queue", **state})
        while True:
            await sock.send_json(await q.get())
    except WebSocketDisconnect:
        pass
    finally:
        bus.unsubscribe(q)
