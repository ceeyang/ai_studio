"""任务类型 → 处理函数。pipeline 模块在导入时用 HANDLERS.register 登记。"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from backend.queue.errors import JobError


@dataclass(frozen=True)
class HandlerSpec:
    type: str
    fn: Callable[[Any, dict], dict | None]
    gpu: bool = False


class HandlerRegistry:
    def __init__(self):
        self._specs: dict[str, HandlerSpec] = {}

    def register(self, job_type: str, *, gpu: bool = False):
        def deco(fn):
            self._specs[job_type] = HandlerSpec(job_type, fn, gpu)
            return fn
        return deco

    def get(self, job_type: str) -> HandlerSpec:
        try:
            return self._specs[job_type]
        except KeyError:
            raise JobError(f"未知任务类型 {job_type}") from None

    def types(self) -> list[str]:
        return sorted(self._specs)


HANDLERS = HandlerRegistry()
