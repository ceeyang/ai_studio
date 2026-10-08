"""镜头重新编号：order 从 0 连续，code 为 S01、S02…"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.domain.models import Shot


def renumber_shots(s: Session, episode_id: int, ordered_ids: list[int] | None = None) -> list[Shot]:
    shots = list(s.scalars(select(Shot).where(Shot.episode_id == episode_id)
                           .order_by(Shot.order, Shot.id)))
    if ordered_ids is not None:
        by_id = {sh.id: sh for sh in shots}
        if sorted(ordered_ids) != sorted(by_id):
            raise ValueError("shot_ids 必须正好是这一集的全部镜头")
        shots = [by_id[i] for i in ordered_ids]
    for i, sh in enumerate(shots):
        sh.order = i
        sh.code = f"S{i + 1:02d}"
    s.flush()
    return shots
