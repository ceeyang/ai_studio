"""把镜头字段拼成最终提示词，并算输入哈希（用于判断候选是否过期）。"""
from __future__ import annotations

from backend.domain.hashing import input_hash


def compose_image_prompt(image_prompt: str, anchor: str) -> str:
    p, a = (image_prompt or "").strip(), (anchor or "").strip()
    return p if not a or a in p else f"{a} {p}"


def compose_video_prompt(motion_prompt: str, image_prompt: str, audio_prompt: str,
                         dialogue: list[dict], *, anchor: str, has_audio: bool) -> str:
    text = compose_image_prompt((motion_prompt or "").strip() or image_prompt, anchor)
    if not has_audio:
        return text
    audio = (audio_prompt or "").strip()
    lines = [f'{d.get("speaker", "")} says in Mandarin Chinese: "{d["zh"]}"'
             for d in dialogue or [] if d.get("zh") and d["zh"] not in audio]
    parts = [p for p in (audio, " ".join(lines)) if p]
    return text + ("\nAudio: " + " ".join(parts) if parts else "")


def join_negative(*parts: str | None) -> str:
    return ", ".join(p.strip() for p in parts if p and p.strip())


def shot_seed(base: int, shot_id: int, n: int) -> int:
    return (int(base) + shot_id * 1009 + n * 7919) % 2**32


def _anchor(style) -> str:
    return style.anchor_text if style else ""


def _negative(project, style) -> str:
    return join_negative(project.negative, style.negative if style else "")


def _refs(ref_ids) -> dict:
    # 没有参考图时哈希与加参考图之前完全一样，老候选不会因此被判过期
    return {"refs": list(ref_ids)} if ref_ids else {}


def keyframe_input_hash(shot, project, style, engine_name: str, ref_ids=()) -> str:
    """ref_ids：引擎实际用到的定妆照候选 id，换了定妆照首帧就过期。"""
    return input_hash("first_frame", compose_image_prompt(shot.image_prompt, _anchor(style)),
                      _negative(project, style), engine_name, project.aspect, **_refs(ref_ids))


def video_input_hash(shot, project, style, first_frame_candidate_id: int, engine_name: str,
                     has_audio: bool, ref_ids=()) -> str:
    prompt = compose_video_prompt(shot.motion_prompt, shot.image_prompt, shot.audio_prompt,
                                  shot.dialogue, anchor=_anchor(style), has_audio=has_audio)
    return input_hash("video", prompt, first_frame_candidate_id, engine_name, project.aspect,
                      round(float(shot.duration), 2), **_refs(ref_ids))


# ------------------------------------------------------------ 输入摘要（候选 params["inputs"]）
# 哈希只能说「变了」，摘要能说「哪里变了」：首帧 / 视频过期时给出原因。
LEGACY_STALE = "输入已变化（旧记录无法细分）"


def ref_inputs(refs) -> list[dict]:
    return [{"id": r.candidate_id, "label": r.label} for r in refs]


def keyframe_inputs(shot, project, style, engine_name: str, refs=()) -> dict:
    """refs：引擎实际用到的参考图（domain.refs.Ref），与 keyframe_input_hash 的 ref_ids 一一对应。"""
    return {"prompt": compose_image_prompt(shot.image_prompt, _anchor(style)),
            "negative": _negative(project, style), "engine": engine_name,
            "aspect": project.aspect, "refs": ref_inputs(refs)}


def video_inputs(shot, project, style, first_frame_candidate_id: int, engine_name: str,
                 has_audio: bool, refs=()) -> dict:
    prompt = compose_video_prompt(shot.motion_prompt, shot.image_prompt, shot.audio_prompt,
                                  shot.dialogue, anchor=_anchor(style), has_audio=has_audio)
    return {"prompt": prompt, "negative": _negative(project, style), "engine": engine_name,
            "aspect": project.aspect, "refs": ref_inputs(refs),
            "duration": round(float(shot.duration), 2), "first_frame": first_frame_candidate_id}


def _refs_reason(old: list, new: list) -> str | None:
    o = {x.get("label"): x.get("id") for x in old if isinstance(x, dict)}
    n = {x.get("label"): x.get("id") for x in new if isinstance(x, dict)}
    parts = ([f"{lb}换了" for lb in n if lb in o and o[lb] != n[lb]]
             + [f"加了 {lb}" for lb in n if lb not in o]
             + [f"去掉了 {lb}" for lb in o if lb not in n])
    if parts:
        return "、".join(parts)
    if [x.get("id") for x in old if isinstance(x, dict)] != [x["id"] for x in new]:
        return "参考图顺序变了"
    return None


def stale_reason(old, new: dict) -> str:
    """选定候选过期的原因，按 参考图 → 提示词 → 引擎 → 画幅 → 时长 → 首帧 取第一条。
    old：候选 params["inputs"]（老候选没有）；new：按镜头当前状态算的同结构摘要。"""
    if not isinstance(old, dict) or not old:
        return LEGACY_STALE
    r = _refs_reason(old.get("refs") or [], new.get("refs") or [])
    if r:
        return r
    if old.get("prompt") != new.get("prompt") or old.get("negative", "") != new.get("negative", ""):
        return "提示词改了"
    if old.get("engine") != new.get("engine"):
        return "引擎换了"
    if old.get("aspect") != new.get("aspect"):
        return "画幅改了"
    if "duration" in new and old.get("duration") != new["duration"]:
        return "时长改了"
    if "first_frame" in new and old.get("first_frame") != new["first_frame"]:
        return "首帧换了"
    return "输入已变化"
