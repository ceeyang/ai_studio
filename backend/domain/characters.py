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


# ---------------------------------------------------------------- 换造型后把镜头里的旧外貌换掉
def known_looks(s: Session, char: Character, extra=()) -> list[str]:
    """这个角色用过的外貌提示词（不含当前的）：调用方给的旧外貌、定妆照候选记着的、AI 推荐方案里的。
    按长度从长到短，替换时先换长的。"""
    from sqlalchemy import select

    from backend.domain.models import Slot

    out: list[str] = [x for x in extra if isinstance(x, str)]
    slot = s.scalar(select(Slot).where(Slot.owner_type == "character", Slot.owner_id == char.id,
                                       Slot.kind == "portrait"))
    for c in (slot.candidates if slot is not None else []):
        out.append((c.params or {}).get("visual_prompt") or "")
    for opt in char.design_options or []:
        if isinstance(opt, dict):
            out.append(opt.get("visual_prompt") or "")
    cur = (char.visual_prompt or "").strip()
    seen: set[str] = set()
    looks = []
    for x in (y.strip() for y in out if isinstance(y, str)):
        if len(x) >= 20 and x != cur and x not in seen:
            seen.add(x)
            looks.append(x)
    return sorted(looks, key=len, reverse=True)


def _variants(look: str) -> list[str]:
    from backend.pipeline.prompting import face_look, upper_body_look

    out = []
    for v in (look, look.rstrip(" ."), upper_body_look(look), upper_body_look(look).rstrip(" ."),
              face_look(look), face_look(look).rstrip(" .")):
        v = v.strip()
        if len(v) >= 20 and v not in out:
            out.append(v)
    return sorted(out, key=len, reverse=True)


def refresh_shot_looks(s: Session, char: Character, extra_old=()) -> dict:
    """「把镜头里的外貌更新为当前造型」：镜头 image_prompt 里一字不差抄着旧外貌（整段、去掉下半身的、
    只留脸的）的地方，换成这个角色的外貌占位符——出图时按景别填当前外貌，以后再换造型也自动跟上。
    只做精确替换：分镜按景别改写过的外貌对不上，列进 unmatched，让用户手改或重写分镜。"""
    from sqlalchemy import select

    from backend.domain.models import Episode, Shot
    from backend.pipeline.prompting import char_token

    token = char_token(char.id)
    olds = [v for look in known_looks(s, char, extra_old) for v in _variants(look)]
    olds = sorted(dict.fromkeys(olds), key=len, reverse=True)
    rows = s.execute(select(Shot, Episode.number).join(Episode, Shot.episode_id == Episode.id)
                     .where(Episode.project_id == char.project_id)
                     .order_by(Episode.number, Shot.order, Shot.id)).all()
    updated, already, unmatched = [], [], []
    replacements = 0
    for shot, number in rows:
        label = f"第{number}集 {shot.code or shot.id}"
        text = shot.image_prompt or ""
        n = 0
        for old in olds:
            k = text.count(old)
            if k:
                text = text.replace(old, token)
                n += k
        if n:
            shot.image_prompt = text
            replacements += n
            updated.append(label)
            continue
        in_cast = any(isinstance(c, dict) and c.get("character_id") == char.id
                      for c in shot.cast or [])
        if token in text:
            already.append(label)
        elif in_cast:
            unmatched.append(label)
    s.flush()
    return {"updated": updated, "replacements": replacements, "already": already,
            "unmatched": unmatched}
