"""角色定妆照：存在 Slot(owner_type="character", kind="portrait") 里，选定的那张给首帧/视频当参考图
（打包见 domain/refs.py）。"""
from __future__ import annotations

import hashlib
from pathlib import Path

from sqlalchemy.orm import Session

from backend.domain.media import MediaStore
from backend.domain.models import Candidate, Character
from backend.domain.slots import add_candidate, get_or_create_slot, select_candidate, selected_candidate

PORTRAIT_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


def portrait_ext(filename: str) -> str:
    ext = Path(filename or "").suffix.lower()
    if ext not in PORTRAIT_EXTS:
        raise ValueError(f"定妆照只支持 {', '.join(sorted(PORTRAIT_EXTS))}，收到：{filename}")
    return ext


def set_portrait(s: Session, media: MediaStore, char: Character, data: bytes,
                 filename: str) -> Candidate:
    """把图片存成该角色的一张定妆照候选并选定。"""
    ext = portrait_ext(filename)
    if not data:
        raise ValueError("定妆照文件是空的")
    dst = media.new_media_path(char.project.slug, "portrait", ext)
    dst.write_bytes(data)
    slot = get_or_create_slot(s, "character", char.id, "portrait")
    c = add_candidate(s, slot, file_path=media.rel(dst), engine="upload",
                      params={"source": Path(filename).name},
                      input_hash=hashlib.sha256(data).hexdigest()[:16], job_id=None,
                      auto_select=False)
    return select_candidate(s, c.id)


def portrait_of(s: Session, char_id: int) -> Candidate | None:
    return selected_candidate(s, "character", char_id, "portrait")


def add_portrait(s: Session, char_id: int, *, file_path: str, engine: str, params: dict,
                 input_hash: str, job_id: int | None) -> Candidate:
    """AI 抽出来的定妆照：加成候选。已经选定了定妆照就不动（换脸会让出场镜头过期），没有才自动选定。"""
    slot = get_or_create_slot(s, "character", char_id, "portrait")
    c = add_candidate(s, slot, file_path=file_path, engine=engine, params=params,
                      input_hash=input_hash, job_id=job_id, auto_select=False)
    if slot.selected_candidate_id is None:
        select_candidate(s, c.id)
    return c


def use_portrait(s: Session, char: Character, candidate_id: int) -> Candidate:
    """切换角色造型：选定这张定妆照；它记着当时的外貌提示词时，角色提示词一起换过去。

    切走的那张如果没记提示词（比如上传的），先把当前提示词记到它身上，切回来时能恢复。"""
    slot = get_or_create_slot(s, "character", char.id, "portrait")
    c = s.get(Candidate, candidate_id)
    if c is None or c.slot_id != slot.id:
        raise LookupError(f"角色 {char.name} 没有定妆照候选 {candidate_id}")
    cur = s.get(Candidate, slot.selected_candidate_id) if slot.selected_candidate_id else None
    if cur is not None and cur.id != c.id and not (cur.params or {}).get("visual_prompt") \
            and char.visual_prompt:
        cur.params = {**(cur.params or {}), "visual_prompt": char.visual_prompt}
    select_candidate(s, c.id)
    vp = (c.params or {}).get("visual_prompt")
    if isinstance(vp, str) and vp.strip():
        char.visual_prompt = vp
    s.flush()
    return c
