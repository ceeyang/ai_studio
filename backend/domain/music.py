"""剧集配乐：每集一条背景音乐，存在 Slot(owner_type="episode", kind="bgm") 里，选定的那条在导出勾「配乐」
时混进成片（post/music.py）。上传新的就替换（选中新的），去掉配乐就删掉记录（文件留在磁盘上）。"""
from __future__ import annotations

import hashlib
from pathlib import Path

from sqlalchemy.orm import Session

from backend.domain.media import MediaStore
from backend.domain.models import Candidate, Episode
from backend.domain.slots import add_candidate, get_or_create_slot, select_candidate, selected_candidate

BGM_SLOT = "bgm"
MUSIC_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}


def music_ext(filename: str) -> str:
    ext = Path(filename or "").suffix.lower()
    if ext not in MUSIC_EXTS:
        raise ValueError(f"配乐只支持 {', '.join(sorted(MUSIC_EXTS))}，收到：{filename}")
    return ext


def bgm_of(s: Session, episode_id: int) -> Candidate | None:
    return selected_candidate(s, "episode", episode_id, BGM_SLOT)


def set_bgm(s: Session, media: MediaStore, ep: Episode, data: bytes, filename: str) -> Candidate:
    ext = music_ext(filename)
    if not data:
        raise ValueError("配乐文件是空的")
    dst = media.new_media_path(ep.project.slug, "bgm", ext)
    dst.write_bytes(data)
    slot = get_or_create_slot(s, "episode", ep.id, BGM_SLOT)
    c = add_candidate(s, slot, file_path=media.rel(dst), engine="upload",
                      params={"source": Path(filename).name},
                      input_hash=hashlib.sha256(data).hexdigest()[:16], job_id=None,
                      auto_select=False)
    return select_candidate(s, c.id)


def clear_bgm(s: Session, episode_id: int) -> None:
    """去掉配乐：删掉 bgm 槽位和里面的候选（只删记录，文件留在磁盘上）。"""
    from sqlalchemy import delete, select

    from backend.domain.models import Slot
    slot_ids = select(Slot.id).where(Slot.owner_type == "episode", Slot.owner_id == episode_id,
                                     Slot.kind == BGM_SLOT)
    s.execute(delete(Candidate).where(Candidate.slot_id.in_(slot_ids)))
    s.execute(delete(Slot).where(Slot.owner_type == "episode", Slot.owner_id == episode_id,
                                 Slot.kind == BGM_SLOT))
    s.flush()
