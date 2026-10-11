"""首帧 / 视频的参考图打包（设计文档 §6.4）。

首帧发给引擎的顺序固定：场景图 → 镜头构图参考图（最多 2 张）→ 出场角色 → 道具 → 上一镜尾帧。
超出引擎上限时不是简单从后往前丢（那样先丢的是定妆照，脸就没锁住）：split_refs 先给每个出场角色留一张
定妆照，剩下的名额再按 场景图 → 构图参考 → 三视图格 补满；留下的仍按上面的顺序发，丢掉的记进任务日志。
- 场景图：镜头所在段落的场景选定的那张（不出人物，固定场景一致性），见 domain/locations.py。
- 构图参考：用户在镜头抽屉上传的构图 / 动作参考（Slot kind="shot_ref"，槽位里的候选全部都用）。
- 出场角色：先放所有出场角色选定的定妆照（按 cast 顺序），再按同样顺序放每人一张与机位最匹配的
  三视图格（见 turnaround.view_for_shot）。定妆照排在三视图前面，截断时先丢多出来的角度，保住每个角色的脸。
- 道具：目前道具没有参考图，这一段是空的。
- 上一镜尾帧：开了「接上一镜」的镜头不出图，首帧直接取上一镜视频的最后一帧（pipeline/continuity.py），
  所以不会走到参考图打包。

视频只带出场角色，每人一张脸（头像格优先，没有就定妆照，见 _video_cast_refs）：场景和构图已经由首帧
定下，再塞场景图给视频模型只会让它中途剪到空场景（和以前剪到定妆照影棚背景一样）；参考图越多也越容易
中途重新构图。以前每人带定妆照 + 三视图格，老视频的哈希按 legacy_video_refs 认，不判过期。

没有场景图、构图参考和三视图时打包结果和只带定妆照时完全一样，老镜头的输入哈希不变。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from backend.domain.characters import portrait_of
from backend.domain.locations import image_ext, location_image_of, shot_location
from backend.domain.media import MediaStore
from backend.domain.models import Candidate, Character, Slot
from backend.domain.slots import add_candidate, get_or_create_slot
from backend.domain.turnaround import VIEW_LABELS, pick_cell, view_for_shot

SHOT_REF_SLOT = "shot_ref"
MAX_SHOT_REFS = 2


@dataclass(frozen=True)
class Ref:
    candidate_id: int
    file_path: str
    label: str
    kind: str = ""     # location / shot_ref / portrait / turnaround（截断优先级和「锁脸」判断用）
    character_id: int | None = None   # 定妆照 / 三视图格是谁的（接上一镜时挑新入画的角色用）


# 带脸的参考图：首帧带了其中一张才算按定妆照锁过脸
FACE_KINDS = ("portrait", "turnaround")


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


def _cast_refs(s: Session, shot) -> list[Ref]:
    portraits: list[Ref] = []
    cells: list[Ref] = []
    view = view_for_shot(shot)
    for ch in _cast(s, shot):
        p = portrait_of(s, ch.id)
        if p is None:
            continue  # 没有定妆照就不带三视图（三视图是按定妆照锁的脸）
        portraits.append(Ref(p.id, p.file_path, f"{ch.name} 定妆照", "portrait", ch.id))
        picked = pick_cell(s, ch, view)
        if picked is not None:
            v, c = picked
            cells.append(Ref(c.id, c.file_path, f"{ch.name} 三视图·{VIEW_LABELS[v]}", "turnaround",
                             ch.id))
    return portraits + cells


# 视频每个角色只带一张脸：头像格优先（3/4 脸在前；镜头本身是正脸特写时正脸在前），没有头像格就带定妆照
_VIDEO_FACE_VIEWS = ("face_three_quarter", "face_front")


def _video_cast_refs(s: Session, shot) -> list[Ref]:
    """视频参考图（审查 P1-9）：每个出场角色只带 1 张。H3 视频的首帧已经定了场景、服装和构图，参考图只为
    锁脸；以前每人带定妆照 + 三视图格，参考图越多越容易中途重新构图、剪进参考图。没有定妆照的角色不带
    （头像格也是按定妆照锁的脸）。"""
    from backend.domain.turnaround import cell_state

    view = view_for_shot(shot)
    order = (view, *[v for v in _VIDEO_FACE_VIEWS if v != view]) if view in _VIDEO_FACE_VIEWS \
        else _VIDEO_FACE_VIEWS
    out: list[Ref] = []
    for ch in _cast(s, shot):
        p = portrait_of(s, ch.id)
        if p is None:
            continue
        for v in order:
            sel, stale = cell_state(s, ch, v)
            if sel is not None and not stale:
                out.append(Ref(sel.id, sel.file_path, f"{ch.name} 三视图·{VIEW_LABELS[v]}",
                               "turnaround", ch.id))
                break
        else:
            out.append(Ref(p.id, p.file_path, f"{ch.name} 定妆照", "portrait", ch.id))
    return out


def legacy_video_refs(s: Session, shot) -> list[Ref]:
    """以前视频带的参考图（每人定妆照 + 一张三视图格）：老视频的输入哈希按它算，换规则后不判过期。"""
    return _cast_refs(s, shot)


# ------------------------------------------------------------ 镜头构图参考图
def shot_ref_images(s: Session, shot_id: int) -> list[Candidate]:
    from sqlalchemy import select

    slot = s.scalar(select(Slot).where(Slot.owner_type == "shot", Slot.owner_id == shot_id,
                                       Slot.kind == SHOT_REF_SLOT))
    return list(slot.candidates) if slot is not None else []


def add_shot_ref(s: Session, media: MediaStore, shot, slug: str, data: bytes,
                 filename: str) -> Candidate:
    """上传一张构图 / 动作参考图。每个镜头最多 MAX_SHOT_REFS 张；槽位里的候选全部都用，不选定。"""
    ext = image_ext(filename, "构图参考图")
    if not data:
        raise ValueError("构图参考图文件是空的")
    if len(shot_ref_images(s, shot.id)) >= MAX_SHOT_REFS:
        raise ValueError(f"每个镜头最多 {MAX_SHOT_REFS} 张构图参考图，先删掉一张再传")
    dst = media.new_media_path(slug, "shot_ref", ext)
    dst.write_bytes(data)
    slot = get_or_create_slot(s, "shot", shot.id, SHOT_REF_SLOT)
    return add_candidate(s, slot, file_path=media.rel(dst), engine="upload",
                         params={"source": Path(filename).name},
                         input_hash=hashlib.sha256(data).hexdigest()[:16], job_id=None,
                         auto_select=False)


def remove_shot_ref(s: Session, shot_id: int, candidate_id: int) -> None:
    """删掉一张构图参考图（只删记录，文件留在磁盘上）。"""
    c = next((x for x in shot_ref_images(s, shot_id) if x.id == candidate_id), None)
    if c is None:
        raise LookupError(f"镜头 {shot_id} 没有构图参考图 {candidate_id}")
    s.delete(c)
    s.flush()


# ------------------------------------------------------------ 打包
def shot_refs(s: Session, shot, *, video: bool = False) -> list[Ref]:
    """按 §6.4 的顺序打包这一镜的参考图。video=True 时只带出场角色（视频用，每人一张脸）。"""
    if video:
        return _video_cast_refs(s, shot)
    cast = _cast_refs(s, shot)
    head: list[Ref] = []
    loc = shot_location(s, shot)
    img = location_image_of(s, loc.id) if loc is not None else None
    if img is not None:
        head.append(Ref(img.id, img.file_path, f"场景图·{loc.name}", "location"))
    for i, c in enumerate(shot_ref_images(s, shot.id), start=1):
        head.append(Ref(c.id, c.file_path, f"构图参考 {i}", "shot_ref"))
    return head + cast


# 截断时补名额的顺序（定妆照先每个角色留一张，见 split_refs）
_FILL_ORDER = ("location", "shot_ref", "turnaround")


def split_refs(packed: list[Ref], max_refs: int) -> tuple[list[Ref], list[Ref]]:
    """按引擎上限截断，返回 (用到的, 丢掉的)，两边都保持 packed 里的顺序。

    先给每个出场角色留一张定妆照（角色比名额多时按出场顺序留前几个），剩下的名额按
    场景图 → 构图参考 → 三视图格 补；kind 不认识的排在最后。"""
    n = max(int(max_refs), 0)
    if len(packed) <= n:
        return list(packed), []
    keep: set[int] = set()
    for i, r in enumerate(packed):
        if r.kind == "portrait" and len(keep) < n:
            keep.add(i)
    rank = {k: j for j, k in enumerate(_FILL_ORDER)}
    rest = sorted((i for i, r in enumerate(packed) if i not in keep),
                  key=lambda i: (rank.get(packed[i].kind, len(rank)), i))
    for i in rest[:n - len(keep)]:
        keep.add(i)
    used = [r for i, r in enumerate(packed) if i in keep]
    dropped = [r for i, r in enumerate(packed) if i not in keep]
    return used, dropped
