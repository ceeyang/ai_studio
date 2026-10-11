"""把镜头字段拼成最终提示词，并算输入哈希（用于判断候选是否过期）。"""
from __future__ import annotations

import re

from typing import NamedTuple

from backend.domain.hashing import input_hash

# ------------------------------------------------------------ 定妆照用的风格词
# 风格锚是给镜头用的，里面混着场景、时间、天气、光源（雨夜、灯泡、积水……）。定妆照要的是干净的
# 身份参考：只留画质 / 镜头胶片 / 色彩 / 皮肤质感这类词，按分句过滤——分句里有场景词就整句丢。
_PHOTO_TERMS = re.compile(
    r"photo|realis|live-action|cinematic still|\blens\b|\d+\s?mm\b|depth of field|bokeh|\bfilm\b"
    r"|grain|halation|kodak|fuji|anamorphic|focus|saturat|contrast|palette|colou?r|grading"
    r"|\btones?\b|tonal|shadow|highlight|skin|pores?\b|wrinkle|imperfection|texture|detail"
    r"|sharp|resolution|proportion|\blight(ing)?\b|exposure|dynamic range", re.I)
_SCENE_TERMS = re.compile(
    r"\brain|\bwet\b|damp|puddle|night|dawn|dusk|sunset|sunrise|sunlight|sunny|morning|evening"
    r"|noon|midday|daylight|\bday\b|interior|exterior|indoor|outdoor|street|city|urban|alley"
    r"|\brooms?\b|workshop|garage|\bshops?\b|office|kitchen|forest|\bsea\b|beach|snow|fog|mist"
    r"|storm|\bsky|cloud|concrete|reflection|bulb|lamp|neon|fluorescent|\btubes?\b|candle"
    r"|window|lantern|torch|\bfire|\btools?\b|rust|metal|\bsteel|building|road|\bcars?\b"
    r"|vehicle|landscape|scene|location|background|backdrop|environment|atmosphere|weather"
    r"|practical|motivated|across frames", re.I)


def _clauses(text: str) -> list[str]:
    out = []
    for sentence in re.split(r"[.!?;。；]+", text or ""):
        for part in re.split(r"[,:，：]", sentence):
            part = part.strip()
            if part:
                out.append(part)
    return out


def photo_style(anchor: str) -> str:
    """风格锚里只和成像有关的分句（画质、镜头、胶片颗粒、色彩、皮肤质感），去掉场景、时间、天气、光源。"""
    keep = [c for c in _clauses(anchor) if _PHOTO_TERMS.search(c) and not _SCENE_TERMS.search(c)]
    keep = [c if i == 0 or c[1:2].isupper() else c[:1].lower() + c[1:] for i, c in enumerate(keep)]
    return ", ".join(keep)


_LOWER_BODY = re.compile(r"trousers|pants|jeans|shorts|skirt|\bboots?\b|shoes?\b|sneakers?|sandals?"
                         r"|heels|socks|stockings|leggings|\bfeet\b|\blegs?\b|\bknees?\b", re.I)
_UPPER_BODY = re.compile(r"face|hair|eyes?\b|jacket|shirt|coat|sweater|hoodie|blouse|vest|collar"
                         r"|sleeve|neck|shoulder|chest|hands?\b|wrist|\barms?\b|beard|stubble"
                         r"|glasses|earring|\bhat\b|\bcap\b|scarf|windbreaker|cardigan|\btop\b", re.I)


def upper_body_look(visual_prompt: str) -> str:
    """半身照用的外貌：去掉只写下半身（裤子、鞋）的整句，免得模型为了画靴子把镜头拉成全身。
    一句里同时写了上身的不动。"""
    sentences = re.split(r"(?<=[.!?])\s+", (visual_prompt or "").strip())
    kept = [x for x in sentences if not (_LOWER_BODY.search(x) and not _UPPER_BODY.search(x))]
    return " ".join(kept) if kept else (visual_prompt or "").strip()


def compose_image_prompt(image_prompt: str, anchor: str) -> str:
    p, a = (image_prompt or "").strip(), (anchor or "").strip()
    return p if not a or a in p else f"{a} {p}"


class Speaker(NamedTuple):
    """视频提示词里怎么称呼说话人：英文简称（不写中文名），以及他在不在画面里（在这一镜的 cast 里）。"""
    ref: str
    on_screen: bool


_REF_CUT = re.compile(r",|;|\.|\bwith\b|\bwearing\b|\bwho\b|\band\b", re.I)


def short_ref(visual_prompt: str | None) -> str:
    """角色外貌的英文简称：第一个分句（到逗号 / with / wearing 为止，最多 8 个词），前面加 the。
    如「a woman in her late twenties with an oval face」→「the woman in her late twenties」。"""
    text = (visual_prompt or "").strip()
    if not text or _CJK_ANY.search(text):
        return "the speaker"
    head = _REF_CUT.split(text, 1)[0].strip()
    words = head.split()[:8]
    if words and words[0].lower() in ("a", "an", "the"):
        words = words[1:]
    return "the " + " ".join(words) if words else "the speaker"


def _dialogue_line(d: dict, speakers: dict[str, Speaker] | None, anyone_on_screen: bool) -> str:
    said = f'says in Mandarin Chinese: "{d["zh"]}"'
    if speakers is None:   # 老写法：算输入哈希用，保持一字不差，已有视频不会因此过期
        return f'{d.get("speaker", "")} {said}'
    sp = speakers.get(d.get("speaker", ""))
    if sp is not None and sp.on_screen:
        return f"{sp.ref} {said}"
    lips = ", the on-screen character's lips stay closed" if anyone_on_screen else ""
    return f"an off-screen voice {said}{lips}"


def compose_video_prompt(motion_prompt: str, image_prompt: str, audio_prompt: str,
                         dialogue: list[dict], *, anchor: str, has_audio: bool,
                         speakers: dict[str, Speaker] | None = None) -> str:
    """speakers 给了（发给引擎时）：画面里的说话人用英文简称，不在画面里的写成画外音、画面里的人闭嘴；
    不给（算输入哈希、inputs 摘要时）：沿用老写法。"""
    text = compose_image_prompt((motion_prompt or "").strip() or image_prompt, anchor)
    if not has_audio:
        return text
    audio = (audio_prompt or "").strip()
    anyone = any(sp.on_screen for sp in (speakers or {}).values())
    lines = [_dialogue_line(d, speakers, anyone)
             for d in dialogue or [] if d.get("zh") and d["zh"] not in audio]
    parts = [p for p in (audio, " ".join(lines)) if p]
    return text + ("\nAudio: " + " ".join(parts) if parts else "")


# 视频连续性：H3 带参考图时曾在镜头中途切到定妆照（灰色影棚背景、正面站立）。只拼进发给引擎的提示词，
# 不进输入哈希和 inputs 摘要——否则模板一改，所有已出的视频都会被判过期。
# 用正面写法点名这一镜的地点：「no studio background」这类否定词反而会把影棚背景招进画面。
_CJK_ANY = re.compile(r"[⺀-鿿가-힯豈-﫿＀-￯]")


def location_summary(visual_prompt: str | None, max_words: int = 30) -> str:
    """场景外观的第一句（最多 max_words 个词），句首冠词小写，拼进「inside …」；有中文就不用。"""
    text = (visual_prompt or "").strip()
    if not text or _CJK_ANY.search(text):
        return ""
    first = re.split(r"(?<=[.!?])\s+", text)[0].rstrip(" .!?;")
    words = first.split()
    if len(words) > max_words:
        first = " ".join(words[:max_words]).rstrip(",;")
    if re.match(r"(A|An|The)\b", first):
        first = first[0].lower() + first[1:]
    return first


def continuity_clause(location: str = "") -> str:
    if location:
        return (f"One continuous take inside {location}; the background stays this same place for "
                "the whole clip, matching the first frame. No cut.")
    return ("One continuous take; the background stays the same place shown in the first frame "
            "for the whole clip. No cut.")


def with_continuity(video_prompt: str, location: str = "") -> str:
    """把连续性要求接在画面描述后面、Audio 段前面。location 是 location_summary 的结果。"""
    body, sep, audio = (video_prompt or "").partition("\nAudio:")
    body = body.rstrip()
    return f"{body} {continuity_clause(location)}".strip() + (sep + audio if sep else "")


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
