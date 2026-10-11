"""历史候选：删镜头前把它槽位里的候选挪进 archived_candidates，之后可以恢复到同一集的别的镜头上。

为什么单独一张表、而不是给 Candidate 加 archived_at：镜头行会被删掉，槽位挂在镜头上（没有外键），
留着槽位/候选会变成孤儿、新镜头还可能撞上；所以把候选连同镜头当时的快照（编号、提示词、对白……）
抄一份到独立的表里，只挂在剧集上（删集时一起删）。媒体文件一直在磁盘上，不动。"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.domain.models import ArchivedCandidate, Candidate, Episode, Shot, Slot, utcnow
from backend.domain.slots import delete_slots, get_or_create_slot

SNAPSHOT_FIELDS = ("code", "order", "shot_size", "camera_move", "action", "dialogue", "cast",
                   "props", "image_prompt", "motion_prompt", "audio_prompt", "duration",
                   "transition_out", "origin")


def shot_snapshot(sh: Shot) -> dict:
    return {f: getattr(sh, f) for f in SNAPSHOT_FIELDS}


def archive_shot_candidates(s: Session, shot_ids, reason: str) -> int:
    """把这些镜头所有槽位里的候选抄进历史候选，返回抄了几条（不删任何东西）。"""
    ids = list(shot_ids)
    if not ids:
        return 0
    shots = {sh.id: sh for sh in s.scalars(select(Shot).where(Shot.id.in_(ids)))}
    project_of: dict[int, int] = {}
    n = 0
    slots = s.scalars(select(Slot).where(Slot.owner_type == "shot", Slot.owner_id.in_(ids))
                      .order_by(Slot.id))
    for sl in slots:
        sh = shots.get(sl.owner_id)
        if sh is None:
            continue
        if sh.episode_id not in project_of:
            project_of[sh.episode_id] = s.get(Episode, sh.episode_id).project_id
        snap = shot_snapshot(sh)
        for c in sl.candidates:
            s.add(ArchivedCandidate(
                project_id=project_of[sh.episode_id], episode_id=sh.episode_id,
                shot_code=sh.code or "", kind=sl.kind, file_path=c.file_path, engine=c.engine,
                params=dict(c.params or {}), input_hash=c.input_hash or "", note=c.note or "",
                was_selected=sl.selected_candidate_id == c.id, shot_snapshot=snap,
                reason=reason, created_at=c.created_at or utcnow(), archived_at=utcnow()))
            n += 1
    s.flush()
    return n


def archive_and_delete_slots(s: Session, shot_ids, reason: str) -> int:
    """删镜头前清槽位：候选先进历史候选，再删槽位和候选。返回进了历史候选的条数。"""
    ids = list(shot_ids)
    n = archive_shot_candidates(s, ids, reason)
    delete_slots(s, "shot", ids)
    return n


def restore_archived(s: Session, archived_id: int, shot_id: int) -> Candidate:
    """把历史候选恢复到镜头同类槽位上（首帧回首帧、视频回视频），恢复后从历史里去掉。
    槽位还没有选定时恢复的就是选定；已有选定不抢（用户在候选里自己挑）。"""
    a = s.get(ArchivedCandidate, archived_id)
    if a is None:
        raise LookupError(f"找不到历史候选 {archived_id}")
    shot = s.get(Shot, shot_id)
    if shot is None:
        raise LookupError(f"找不到镜头 {shot_id}")
    if shot.episode_id != a.episode_id:
        raise ValueError("只能恢复到同一集的镜头上")
    from backend.domain.refs import MAX_SHOT_REFS, SHOT_REF_SLOT, shot_ref_images
    is_ref = a.kind == SHOT_REF_SLOT
    if is_ref and len(shot_ref_images(s, shot.id)) >= MAX_SHOT_REFS:
        raise ValueError(f"这个镜头已经有 {MAX_SHOT_REFS} 张构图参考图，先删掉一张再恢复")
    slot = get_or_create_slot(s, "shot", shot.id, a.kind)
    c = Candidate(slot_id=slot.id, file_path=a.file_path, engine=a.engine,
                  params=dict(a.params or {}), input_hash=a.input_hash, job_id=None,
                  note=a.note, created_at=a.created_at)
    s.add(c)
    s.flush()
    # 构图参考图槽位里的图全部都用、不选定
    if not is_ref and (slot.selected_candidate_id is None
                       or s.get(Candidate, slot.selected_candidate_id) is None):
        slot.selected_candidate_id = c.id
    s.delete(a)
    s.flush()
    return c
