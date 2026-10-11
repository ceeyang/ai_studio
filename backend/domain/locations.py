"""场景图：每个场景一张「不出人物」的环境参考图，存在 Slot(owner_type="location", kind="location_image")
里，选定的那张排在首帧参考图的最前面（打包见 domain/refs.py）。镜头靠所在段落的场景找到它。"""
from __future__ import annotations

import hashlib
from pathlib import Path

from sqlalchemy.orm import Session

from backend.domain.media import MediaStore
from backend.domain.models import Candidate, Location, SceneBlock
from backend.domain.slots import add_candidate, get_or_create_slot, select_candidate, selected_candidate

LOCATION_SLOT = "location_image"
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


def image_ext(filename: str, what: str = "场景图") -> str:
    ext = Path(filename or "").suffix.lower()
    if ext not in IMAGE_EXTS:
        raise ValueError(f"{what}只支持 {', '.join(sorted(IMAGE_EXTS))}，收到：{filename}")
    return ext


def location_spec(loc) -> str:
    """场景图按哪份设定出的：名字、时段、英文画面提示词（这几样进出图提示词；氛围不进）。
    AI 出的场景图把它记在 params["spec"]，设定改了选定的那张就标过期。"""
    from backend.domain.hashing import input_hash
    return input_hash("location_spec", (loc.name or "").strip(), (loc.time_of_day or "").strip(),
                      (loc.visual_prompt or "").strip())


def location_image_stale(loc, cand: Candidate | None) -> bool:
    """选定的场景图是按旧设定出的。上传的和老记录（没有 spec）不算：没法知道它对应哪份设定。"""
    spec = ((cand.params or {}).get("spec") if cand is not None else None)
    return bool(spec) and spec != location_spec(loc)


def location_image_of(s: Session, loc_id: int) -> Candidate | None:
    return selected_candidate(s, "location", loc_id, LOCATION_SLOT)


def shot_location(s: Session, shot) -> Location | None:
    """镜头所在段落的场景；手工镜头没挂段落、段落没挂场景时为 None。"""
    blk = s.get(SceneBlock, shot.scene_block_id) if shot.scene_block_id else None
    return s.get(Location, blk.location_id) if blk is not None and blk.location_id else None


def add_location_image(s: Session, loc_id: int, *, file_path: str, engine: str, params: dict,
                       input_hash: str, job_id: int | None) -> Candidate:
    """AI 出的场景图：加成候选。已经选定了就不动（换场景图会让这个场景的镜头首帧过期），没有才自动选定。"""
    slot = get_or_create_slot(s, "location", loc_id, LOCATION_SLOT)
    c = add_candidate(s, slot, file_path=file_path, engine=engine, params=params,
                      input_hash=input_hash, job_id=job_id, auto_select=False)
    if slot.selected_candidate_id is None:
        select_candidate(s, c.id)
    return c


def set_location_image(s: Session, media: MediaStore, loc: Location, data: bytes,
                       filename: str) -> Candidate:
    """上传的场景图：存成候选并选定（用户明确要用这张）。"""
    ext = image_ext(filename)
    if not data:
        raise ValueError("场景图文件是空的")
    dst = media.new_media_path(loc.project.slug, "location", ext)
    dst.write_bytes(data)
    slot = get_or_create_slot(s, "location", loc.id, LOCATION_SLOT)
    c = add_candidate(s, slot, file_path=media.rel(dst), engine="upload",
                      params={"source": Path(filename).name},
                      input_hash=hashlib.sha256(data).hexdigest()[:16], job_id=None,
                      auto_select=False)
    return select_candidate(s, c.id)
