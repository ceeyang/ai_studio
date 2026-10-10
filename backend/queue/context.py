"""处理器拿到的上下文：写日志、报进度、检查取消、访问服务。"""
from __future__ import annotations

import threading
from typing import Any

from backend.queue.errors import JobCanceled


class JobContext:
    def __init__(self, manager: Any, job_id: int, cancel_event: threading.Event):
        self.manager = manager
        self.job_id = job_id
        self.cancel_event = cancel_event

    @property
    def services(self) -> Any:
        return self.manager.services

    def log(self, msg: str) -> None:
        self.manager.append_log(self.job_id, msg)

    def progress(self, value: int, maximum: int) -> None:
        store = getattr(self.manager, "progress", None)
        if maximum and isinstance(store, dict):
            store[self.job_id] = max(0.0, min(1.0, value / maximum))
        self.manager.bus.publish({"type": "progress", "job_id": self.job_id,
                                  "value": value, "max": maximum})

    def check_canceled(self) -> None:
        if self.cancel_event.is_set():
            raise JobCanceled()
