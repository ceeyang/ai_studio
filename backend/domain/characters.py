"""角色定妆照：存在 Slot(owner_type="character", kind="portrait") 里，选定的那张给首帧/视频当参考图
（打包见 domain/refs.py）。"""
from __future__ import annotations

import hashlib
import re
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


# ---------------------------------------------------------------- 外观参考照片
# 「从参考图写外观」上传的照片：只给 LLM 看、让它写外观提示词，不是出图参考图（refs.py 不读它）。
# 一个角色一张（新传的换掉旧的选定），LLM 写好的外观记在这张照片候选的 params["look"] 里，等用户确认填入。
APPEARANCE_REF = "appearance_ref"


def set_appearance_ref(s: Session, media: MediaStore, char: Character, data: bytes,
                       filename: str) -> Candidate:
    ext = Path(filename or "").suffix.lower()
    if ext not in PORTRAIT_EXTS:
        raise ValueError(f"参考照片只支持 {', '.join(sorted(PORTRAIT_EXTS))}，收到：{filename}")
    if not data:
        raise ValueError("参考照片是空的")
    dst = media.new_media_path(char.project.slug, APPEARANCE_REF, ext)
    dst.write_bytes(data)
    slot = get_or_create_slot(s, "character", char.id, APPEARANCE_REF)
    c = add_candidate(s, slot, file_path=media.rel(dst), engine="upload",
                      params={"source": Path(filename).name},
                      input_hash=hashlib.sha256(data).hexdigest()[:16], job_id=None,
                      auto_select=False)
    return select_candidate(s, c.id)


def appearance_ref_of(s: Session, char_id: int) -> Candidate | None:
    return selected_candidate(s, "character", char_id, APPEARANCE_REF)


def set_photo_look(s: Session, cand: Candidate, look: dict | None) -> None:
    """记下（或清掉）LLM 按这张照片写的外观：{"visual_prompt", "core_features", "job_id"}。"""
    params = {k: v for k, v in (cand.params or {}).items() if k != "look"}
    if look:
        params["look"] = look
    cand.params = params
    s.flush()


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


def _variants(look: str, shot_size: str = "") -> list[str]:
    """这一镜可能抄进去的旧外貌写法：整段，以及按这一镜景别取舍后的那一段（和出图时一致）。
    不再试别的景别的写法——特写的「只留脸」去匹配中景，会只换掉第一句、把旧服装留在提示词里。"""
    from backend.pipeline.prompting import look_for_size

    out: list[str] = []
    for v in (look, look_for_size(look, shot_size)):
        for w in (v.strip(), v.strip().rstrip(" .")):
            if len(w) >= 20 and w not in out:
                out.append(w)
    return sorted(out, key=len, reverse=True)


_WORDS = re.compile(r"[a-z0-9][a-z0-9'-]*")
LEFTOVER_NGRAM = 6


def _leftover(text: str, looks: list[str]) -> bool:
    """替换后提示词里还留着旧外貌：某一整句（4 个词以上）原样还在，或有连续 6 个词和旧外貌一样。"""
    words = _WORDS.findall((text or "").lower())
    grams = {tuple(words[i:i + LEFTOVER_NGRAM]) for i in range(len(words) - LEFTOVER_NGRAM + 1)}
    for look in looks:
        for sent in re.split(r"(?<=[.!?])\s+", look):
            sent = sent.strip().rstrip(" .")
            if len(sent.split()) >= 4 and sent in text:
                return True
        ow = _WORDS.findall(look.lower())
        if any(tuple(ow[i:i + LEFTOVER_NGRAM]) in grams for i in range(len(ow) - LEFTOVER_NGRAM + 1)):
            return True
    return False


def refresh_shot_looks(s: Session, char: Character, extra_old=(),
                       changed: dict[int, str] | None = None) -> dict:
    """「把镜头里的外貌更新为当前造型」：镜头 image_prompt 里一字不差抄着旧外貌（整段，或按这一镜景别
    取舍后的那段）的地方，换成这个角色的外貌占位符——出图时按景别填当前外貌，以后再换造型也自动跟上。

    只做干净的替换：换完以后提示词里还留着旧外貌的整句或连续 6 个词（分镜只抄了一部分、其余改写过），
    就不换，列进 partial；完全对不上的列进 unmatched。两种都要用户手改或重写这几段分镜。
    changed：换过的镜头 id → 换之前的镜头内容哈希（给分镜记录跟着更新用）。"""
    from sqlalchemy import select

    from backend.domain.models import Episode, Shot
    from backend.pipeline.prompting import char_token
    from backend.pipeline.storyboard import shot_content_hash

    token = char_token(char.id)
    looks = known_looks(s, char, extra_old)
    rows = s.execute(select(Shot, Episode.number).join(Episode, Shot.episode_id == Episode.id)
                     .where(Episode.project_id == char.project_id)
                     .order_by(Episode.number, Shot.order, Shot.id)).all()
    updated, already, unmatched, partial = [], [], [], []
    replacements = 0
    for shot, number in rows:
        label = f"第{number}集 {shot.code or shot.id}"
        before = shot.image_prompt or ""
        olds = sorted(dict.fromkeys(v for look in looks for v in _variants(look, shot.shot_size or "")),
                      key=len, reverse=True)
        text, n = before, 0
        for old in olds:
            k = text.count(old)
            if k:
                text = text.replace(old, token)
                n += k
        in_cast = any(isinstance(c, dict) and c.get("character_id") == char.id
                      for c in shot.cast or [])
        if n and _leftover(text, looks):
            partial.append(label)
            continue
        if n:
            if changed is not None:
                changed[shot.id] = shot_content_hash(shot)
            shot.image_prompt = text
            replacements += n
            updated.append(label)
            continue
        if token in before:
            already.append(label)
        elif in_cast:
            unmatched.append(label)
    s.flush()
    return {"updated": updated, "replacements": replacements, "already": already,
            "unmatched": unmatched, "partial": partial}


# 剧本结构模板要求只出声的人在 description 里写明「只出声，不出镜」（画外音、电话里的声音）
_VOICE_ONLY = re.compile(r"只出声|不出镜|只闻其声|只有声音")


def voice_only_description(description: str | None) -> bool:
    """角色描述写明了只出声、不出镜。"""
    return bool(_VOICE_ONLY.search(description or ""))
