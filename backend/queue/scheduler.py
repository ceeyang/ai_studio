"""选下一个任务：优先级最高的先跑；同优先级优先选与当前已加载模型同族的，减少换模型。"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class QueuedJob:
    id: int
    priority: int
    model_family: str | None


def pick_next(jobs: Sequence[QueuedJob], current_family: str | None) -> QueuedJob | None:
    if not jobs:
        return None
    top = max(j.priority for j in jobs)
    pool = [j for j in jobs if j.priority == top]
    same = [j for j in pool if current_family and j.model_family == current_family]
    return min(same or pool, key=lambda j: j.id)
