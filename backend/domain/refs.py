"""首帧 / 视频的参考图打包（设计文档 §6.4）。

顺序固定：场景图 → 出场角色 → 道具 → 上一镜尾帧，超出引擎上限时从后往前丢。
目前只有角色有参考图：先放所有出场角色选定的定妆照（按 cast 顺序），再按同样顺序放每人
一张与机位最匹配的三视图格（见 turnaround.view_for_shot）。定妆照排在三视图前面，
截断时先丢多出来的角度，保住每个角色的脸。

没有三视图时打包结果和只带定妆照时完全一样，老镜头的输入哈希不变。
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from backend.domain.characters import portrait_of
from backend.domain.models import Character
from backend.domain.turnaround import VIEW_LABELS, pick_cell, view_for_shot


@dataclass(frozen=True)
class Ref:
    candidate_id: int
    file_path: str
    label: str


def _cast(s: Session, shot) -> list[Character]:
    out: list[Character] = []
    seen: set[int] = set()
    for entry in shot.cast or []:
        cid = entry.get("character_id") if isinstance(entry, dict) else None
        if cid is None or cid in seen:
            continue
        seen.add(cid)
        ch = s.get(Character, cid)
        if ch is not None:
            out.append(ch)
    return out


def shot_refs(s: Session, shot) -> list[Ref]:
    chars = _cast(s, shot)
    portraits: list[Ref] = []
    cells: list[Ref] = []
    view = view_for_shot(shot)
    for ch in chars:
        p = portrait_of(s, ch.id)
        if p is None:
            continue  # 没有定妆照就不带三视图（三视图是按定妆照锁的脸）
        portraits.append(Ref(p.id, p.file_path, f"{ch.name} 定妆照"))
        picked = pick_cell(s, ch, view)
        if picked is not None:
            v, c = picked
            cells.append(Ref(c.id, c.file_path, f"{ch.name} 三视图·{VIEW_LABELS[v]}"))
    return portraits + cells
