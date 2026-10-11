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
    r"|practical|motivated|across frames"
    # 氛围和时段光：定妆照的灰背景上会画出烟雾、暖色偏色（第二次样片的「atmospheric haze」）
    r"|haze|hazy|smok|\bdust|steam|atmospher|backlit|rim light|golden.hour|sidelight|sunlit", re.I)
# 句中不该改成小写的专有名词（胶片、相机、镜头品牌）
_PROPER = re.compile(r"(?:Kodak|Fuji|Fujifilm|ARRI|Alexa|RED|Cooke|Zeiss|Leica|Canon|Sony|Panavision"
                     r"|Ektachrome|Portra|CineStill|Cinestill|Vision3|Ilford)\b")


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
    keep = [c if i == 0 or c[1:2].isupper() or _PROPER.match(c) else c[:1].lower() + c[1:]
            for i, c in enumerate(keep)]
    return ", ".join(keep)


_LOWER_BODY = re.compile(r"trousers|\bpants\b|jeans|\bshorts\b|skirt|\bboots?\b"
                         r"|\bshoes?\b(?!\s+polish)|sneakers?|sandals?|slippers?|loafers?|\bheels\b"
                         r"|\bsocks?\b|stockings|leggings|\btights\b|\bfeet\b|\bfoot\b|\blegs?\b"
                         r"|\bknees?\b|\bankles?\b|\bthighs?\b|\bhips?\b|\bshins?\b", re.I)
_UPPER_BODY = re.compile(r"face|hair|eyes?\b|jacket|shirt|coat|sweater|hoodie|blouse|vest|collar"
                         r"|sleeve|neck|shoulder|chest|hands?\b|wrist|\barms?\b|beard|stubble"
                         r"|glasses|earring|\bhat\b|\bcap\b|scarf|windbreaker|cardigan|\btop\b"
                         r"|apron|uniform|cardigan|\bdress\b|pinafore|overalls", re.I)
# 定妆照（腰部以上、站着）用不上的：坐 / 蹲 / 跪这类姿势，以及手部细节——手在腰部以下或画面边缘，
# 「老茧」「缠线」这类细小特征 cfg 1 的模型常画错（老茧画成金戒指），再经参考图带进每个镜头
_POSE = re.compile(r"\bsitting\b|\bseated\b|\bsits\b|squat|crouch|kneel|\blying\b|perched"
                   r"|cross-legged", re.I)
_HAND_DETAIL = re.compile(r"\bhands?\b|fingers?|fingertips?|knuckles?|\bnails?\b|\bpalms?\b"
                          r"|callus|callous|\brings?\b", re.I)
_SUBPART = re.compile(r"(\s+(?:and|with|over|under|plus|above)\s+|\s+tucked into\s+)", re.I)


def _strip_clauses(text: str, drop: re.Pattern, keep: re.Pattern | None) -> str:
    """按句、按逗号分句去掉命中 drop 的部分。分句同时写了 keep（如上衣）时只去掉其中命中 drop 的
    「and / with / over …」子句（「a navy coat over grey trousers」→「a navy coat」）。"""
    out = []
    for sentence in re.split(r"(?<=[.!?])\s+", (text or "").strip()):
        body, end = (sentence[:-1], sentence[-1]) if sentence[-1:] in ".!?" else (sentence, ".")
        kept = []
        for clause in re.split(r",\s*", body):
            if not drop.search(clause):
                kept.append(clause)
                continue
            if keep is None or not keep.search(clause):
                continue
            parts = _SUBPART.split(clause)   # [片段, 连接词, 片段, 连接词, …]
            pieces = [parts[0]] if not drop.search(parts[0]) else []
            for sep, frag in zip(parts[1::2], parts[2::2]):
                if not drop.search(frag):
                    pieces.append((sep if pieces else "") + frag)
            if pieces and keep.search("".join(pieces)):
                kept.append("".join(pieces).strip())
        if kept:
            first = re.sub(r"^(?:and|with)\s+", "", kept[0].strip(), flags=re.I)
            out.append(", ".join([first[:1].upper() + first[1:]] + kept[1:]) + end)
    return " ".join(out)


def upper_body_look(visual_prompt: str) -> str:
    """半身照、近景用的外貌：去掉写下半身（裤子、鞋、腿）的分句，免得模型为了画靴子把镜头拉成全身。
    分句同时写了上身时只去掉下半身那一截。全被去掉时用原文。"""
    return _strip_clauses(visual_prompt, _LOWER_BODY, _UPPER_BODY) or (visual_prompt or "").strip()


def portrait_look(visual_prompt: str) -> str:
    """定妆照用的外貌：在 upper_body_look 的基础上再去掉坐、蹲、跪这类姿势和手部细节。
    krea2 / zimage 是 cfg 1、没有负面提示词，提示词里出现鞋、裤子、坐姿，画面就会拉到全身。"""
    drop = re.compile(f"{_LOWER_BODY.pattern}|{_POSE.pattern}|{_HAND_DETAIL.pattern}", re.I)
    keep = re.compile(_UPPER_BODY.pattern.replace(r"|hands?\b", ""), re.I)
    return _strip_clauses(visual_prompt, drop, keep) or upper_body_look(visual_prompt)


# ------------------------------------------------------------ 角色外貌占位符
# 分镜里 image_prompt 用 {{char:<角色 id>}} 占位，出图时按角色当前的外貌提示词、按景别取舍后填进去：
# 外貌只有一个来源，角色换造型后镜头自动跟上（首帧输入哈希按填好的文字算，换造型就过期）。
CHAR_TOKEN = re.compile(r"\{\{\s*char\s*:\s*(\d+)\s*\}\}")
_FACE = re.compile(r"face|hair|eyes?\b|eyebrows?|brows?\b|nose|lips?\b|mouth|jaw|cheek|chin|skin"
                   r"|complexion|beard|stubble|mustache|moustache|glasses|spectacles|earrings?"
                   r"|freckles?|mole|scar|wrinkle|forehead|bangs|ponytail|bun\b|braid", re.I)
_TOKEN_WITH_TAIL = re.compile(CHAR_TOKEN.pattern + r"(\s*[.,;:!?])?")
FACE_SIZES = ("大特写", "特写")
UPPER_SIZES = ("近景",)


def char_token(char_id: int) -> str:
    return "{{char:%d}}" % char_id


def face_look(visual_prompt: str) -> str:
    """特写用的外貌：第一句（性别、年龄、族裔）加上写脸、头发、皮肤、眼镜这些的句子。"""
    sentences = [x for x in re.split(r"(?<=[.!?])\s+", (visual_prompt or "").strip()) if x]
    if not sentences:
        return ""
    kept = [sentences[0]] + [x for x in sentences[1:] if _FACE.search(x)]
    return " ".join(kept)


def look_for_size(visual_prompt: str, shot_size: str = "") -> str:
    """按景别取舍外貌：大特写、特写只要脸和头发；近景去掉只写下半身的句子；中景、全景用全文。"""
    if shot_size in FACE_SIZES:
        return face_look(visual_prompt)
    if shot_size in UPPER_SIZES:
        return upper_body_look(visual_prompt)
    return (visual_prompt or "").strip()


def resolve_char_tokens(text: str, looks: dict[int, str], shot_size: str = "") -> str:
    """把 {{char:<id>}} 换成该角色按景别取舍后的外貌（句末补句号）；没有占位符时原样返回。
    找不到的角色（删了）或外貌是空的、或是中文的，占位符直接去掉（中文进英文提示词会被画成字）。"""
    if not text or "{{" not in text:
        return text or ""

    def sub(m: re.Match) -> str:
        look = look_for_size(looks.get(int(m.group(1)), "") or "", shot_size).strip()
        tail = m.group(2) or ""   # 占位符后面紧跟的标点（替换旧外貌留下的）：用它代替句号
        if not look or _CJK_ANY.search(look):
            return ""
        if tail:
            return look.rstrip(" .") + tail.strip()
        return look if look[-1] in ".!?" else look + "."

    out = _TOKEN_WITH_TAIL.sub(sub, text)
    return re.sub(r"[ \t]{2,}", " ", out).strip()


def project_looks(project) -> dict[int, str]:
    return {c.id: c.visual_prompt or "" for c in getattr(project, "characters", None) or []}


def shot_image_prompt(shot, project) -> str:
    """镜头的 image_prompt，占位符换成角色当前的外貌（按这一镜的景别取舍）。"""
    text = shot.image_prompt or ""
    if "{{" not in text:
        return text
    return resolve_char_tokens(text, project_looks(project), shot.shot_size or "")


_STILL_WORDS = re.compile(r"\b(?:cinematic\s+)?(?:still(?:\s+(?:image|frame|photo(?:graph)?))?"
                          r"|photograph|photo)s?\b", re.I)
VIDEO_STYLE_WORDS = 25


def video_style(anchor: str, max_words: int = VIDEO_STYLE_WORDS) -> str:
    """视频提示词里的风格：一句短的成像风格（photo_style 再把 still / photograph 这类静帧词换成
    footage，最多 max_words 个词），放在动作后面。画风已经由首帧决定，整段静态风格锚放在视频提示词
    前面只会喧宾夺主（「cinematic still」暗示静帧，锚里的雨、灯泡、工具会被当成要出现、要动的东西）。"""
    style = _STILL_WORDS.sub("footage", photo_style(anchor))
    style = re.sub(r"\bfootage(?:[\s,]+footage)+\b", "footage", style)
    kept: list[str] = []
    for clause in (c.strip() for c in style.split(",")):
        if clause and len(" ".join(kept + [clause]).split()) <= max_words:
            kept.append(clause)
        elif kept:
            break
    if not kept:
        return ""
    text = ", ".join(kept).rstrip(" ,;:.")
    if not text[1:2].isupper():
        text = text[:1].lower() + text[1:]
    return f"Visual style: {text}."


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
    body = (motion_prompt or "").strip() or (image_prompt or "").strip()
    if speakers is None:   # 老写法（算输入哈希、inputs 摘要）：风格锚整段在前，保持一字不差
        text = compose_image_prompt(body, anchor)
    else:                  # 发给引擎：动作在前，后面一句短的成像风格
        text = f"{body} {video_style(anchor)}".strip()
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
    """场景外观的第一句（最多 max_words 个词），拼进「inside …」：句首小写（缩写不动），没有冠词就补
    a / an（「inside Narrow suburban …」→「inside a narrow suburban …」）；有中文就不用。"""
    text = (visual_prompt or "").strip()
    if not text or _CJK_ANY.search(text):
        return ""
    first = re.split(r"(?<=[.!?])\s+", text)[0].rstrip(" .!?;")
    if not first:
        return ""
    if not first[1:2].isupper():   # 「LED」「TV」这种缩写开头不动
        first = first[0].lower() + first[1:]
    if not re.match(r"(a|an|the|this|one)\b", first, re.I):
        first = ("an " if first[0].lower() in "aeiou" else "a ") + first
    words = first.split()
    if len(words) > max_words:
        first = " ".join(words[:max_words]).rstrip(",;")
    return first


def continuity_clause(location: str = "") -> str:
    if location:
        return (f"One continuous take inside {location}; the background stays this same place for "
                "the whole clip, matching the first frame, as a single uninterrupted shot.")
    return ("One continuous take; the background stays the same place shown in the first frame "
            "for the whole clip, as a single uninterrupted shot.")


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
    return input_hash("first_frame", compose_image_prompt(shot_image_prompt(shot, project), _anchor(style)),
                      _negative(project, style), engine_name, project.aspect, **_refs(ref_ids))


def first_frame_locked_face(ff_params: dict | None, image_max_refs: int) -> bool:
    """选定的首帧是不是带定妆照锁过脸。首帧候选的 inputs["refs"] 记着引擎实际用到的参考图；
    老候选没有 inputs 时按出图引擎能不能吃参考图估计（引擎不存在、上传的首帧按没锁脸算）。

    没锁脸（zimage / krea2 文生图）时，首帧里的脸是文字画出来的，和定妆照不是同一张脸：
    视频再带定妆照，H3 会在两张脸之间折中或中途「跳」到参考图。这种镜头视频身份以首帧为准。"""
    inputs = (ff_params or {}).get("inputs")
    if isinstance(inputs, dict) and "refs" in inputs:
        return bool(inputs["refs"])
    return image_max_refs > 0


def video_input_hash(shot, project, style, first_frame_candidate_id: int, engine_name: str,
                     has_audio: bool, ref_ids=()) -> str:
    prompt = compose_video_prompt(shot.motion_prompt, shot_image_prompt(shot, project),
                                  shot.audio_prompt,
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
    return {"prompt": compose_image_prompt(shot_image_prompt(shot, project), _anchor(style)),
            "negative": _negative(project, style), "engine": engine_name,
            "aspect": project.aspect, "refs": ref_inputs(refs)}


def video_inputs(shot, project, style, first_frame_candidate_id: int, engine_name: str,
                 has_audio: bool, refs=()) -> dict:
    prompt = compose_video_prompt(shot.motion_prompt, shot_image_prompt(shot, project),
                                  shot.audio_prompt,
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
