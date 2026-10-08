"""WebSocket：推送任务状态、日志、进度、队列状态。"""
from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool

router = APIRouter(prefix="/api", tags=["ws"])


@router.websocket("/ws")
async def events(sock: WebSocket):
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
