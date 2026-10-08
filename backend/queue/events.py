"""事件总线：worker 线程发布，WebSocket（asyncio）订阅。"""
from __future__ import annotations

import asyncio
import threading


class EventBus:
    def __init__(self, maxsize: int = 1000):
        self._subs: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()
        self._maxsize = maxsize

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self._maxsize)
        with self._lock:
            self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._subs.discard(q)

    def publish(self, event: dict) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(self._fanout, event)
        except RuntimeError:  # loop 已关闭
            pass

    def _fanout(self, event: dict) -> None:
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            if not q.full():  # 慢消费者直接丢事件，不阻塞 worker
                q.put_nowait(event)
