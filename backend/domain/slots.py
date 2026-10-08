"""槽位与候选。下游只使用槽位里选定的那个候选。"""
from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.domain.models import Candidate, Slot


def get_or_create_slot(s: Session, owner_type: str, owner_id: int, kind: str) -> Slot:
    slot = s.scalar(select(Slot).where(Slot.owner_type == owner_type,
                                       Slot.owner_id == owner_id, Slot.kind == kind))
    if slot is None:
        slot = Slot(owner_type=owner_type, owner_id=owner_id, kind=kind)
        s.add(slot)
        s.flush()
    return slot


def add_candidate(s: Session, slot: Slot, *, file_path: str, engine: str, params: dict,
                  input_hash: str, job_id: int | None, auto_select: bool = True) -> Candidate:
    c = Candidate(slot_id=slot.id, file_path=file_path, engine=engine, params=params,
                  input_hash=input_hash, job_id=job_id)
    s.add(c)
    s.flush()
    if auto_select:
        current = s.get(Candidate, slot.selected_candidate_id) if slot.selected_candidate_id else None
        if current is None or current.input_hash != input_hash:
            slot.selected_candidate_id = c.id
    s.flush()
    return c


def select_candidate(s: Session, candidate_id: int) -> Candidate:
    c = s.get(Candidate, candidate_id)
    if c is None:
        raise LookupError(f"找不到候选 {candidate_id}")
    s.get(Slot, c.slot_id).selected_candidate_id = c.id
    s.flush()
    return c


def selected_candidate(s: Session, owner_type: str, owner_id: int, kind: str) -> Candidate | None:
    slot = s.scalar(select(Slot).where(Slot.owner_type == owner_type,
                                       Slot.owner_id == owner_id, Slot.kind == kind))
    if slot is None or slot.selected_candidate_id is None:
        return None
    return s.get(Candidate, slot.selected_candidate_id)


def slots_for(s: Session, owner_type: str, owner_id: int) -> list[Slot]:
    return list(s.scalars(select(Slot).where(Slot.owner_type == owner_type,
                                             Slot.owner_id == owner_id).order_by(Slot.id)))


def delete_slots(s: Session, owner_type: str, owner_ids) -> None:
    """删掉这些对象的槽位和候选（槽位没有外键，删对象时必须手动清）。媒体文件留在磁盘上。"""
    ids = list(owner_ids)
    if not ids:
        return
    slot_ids = select(Slot.id).where(Slot.owner_type == owner_type, Slot.owner_id.in_(ids))
    s.execute(delete(Candidate).where(Candidate.slot_id.in_(slot_ids)))
    s.execute(delete(Slot).where(Slot.owner_type == owner_type, Slot.owner_id.in_(ids)))
    s.flush()
