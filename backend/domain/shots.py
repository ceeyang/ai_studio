"""镜头重新编号：order 从 0 连续，code 为 S01、S02…；镜头视频的生成方式（首帧 / 参考图直出）。"""
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


def prev_shot(s: Session, shot) -> Shot | None:
    """同一集里排在这一镜前面的那一镜（按 order）；第一镜返回 None。"""
    return s.scalar(select(Shot).where(Shot.episode_id == shot.episode_id,
                                       Shot.order < shot.order)
                    .order_by(Shot.order.desc(), Shot.id.desc()).limit(1))


FIRST_FRAME_MODE = "first_frame"
REFS_MODE = "refs"


def refs_mode(shot) -> bool:
    """这一镜实际按参考图直出：选了 refs、而且没开「接上一镜」（接上一镜要用上一镜尾帧当首帧，
    两者互斥时接上一镜优先，按首帧模式出）。"""
    return getattr(shot, "video_mode", None) == REFS_MODE and not getattr(shot, "continue_from_prev", False)


def refs_mode_overridden(shot) -> bool:
    """选了参考图直出、但开了接上一镜，实际按首帧模式出（日志 / 界面说明用）。"""
    return getattr(shot, "video_mode", None) == REFS_MODE and bool(getattr(shot, "continue_from_prev", False))
