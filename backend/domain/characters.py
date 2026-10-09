"""角色定妆照：存在 Slot(owner_type="character", kind="portrait") 里，选定的那张给首帧/视频当参考图。"""
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


def cast_portraits(s: Session, shot) -> list[Candidate]:
    """镜头在场角色（按 cast 顺序、去重）的选定定妆照，没有定妆照的跳过。
    全部交给引擎，超出上限的由引擎丢弃并记日志；输入哈希只算引擎实际用到的前 max_refs 张。"""
    out: list[Candidate] = []
    seen: set[int] = set()
    for entry in shot.cast or []:
        cid = entry.get("character_id") if isinstance(entry, dict) else None
        if cid is None or cid in seen:
            continue
        seen.add(cid)
        c = portrait_of(s, cid)
        if c is not None:
            out.append(c)
    return out
