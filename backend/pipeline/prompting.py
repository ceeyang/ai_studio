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
# 整词匹配（以前子串匹配会误伤：「grain」里有 rain、「metallic sheen」里有 metal）。不含 background、
# metal、rust、daylight——风格锚要写材质真实感、虚化背景，这些词不该让整句被丢
_SCENE_TERMS = re.compile(
    r"\b(?:rain\w*|wet|damp|puddles?|night\w*|dawn|dusk|sunsets?|sunrise|sunlight|sunny|morning"
    r"|evening|noon|midday|day|interiors?|exteriors?|indoors?|outdoors?|streets?|city|cities|urban"
    r"|alleys?|rooms?|workshops?|garages?|shops?|offices?|kitchens?|forests?|sea|beach\w*|snow\w*"
    r"|fog\w*|mist\w*|storm\w*|sky|skies|clouds?|cloudy|concrete|reflections?|bulbs?|lamps?|neon"
    r"|fluorescent|tubes?|candles?|candlelit|windows?|lanterns?|torch\w*|fire\w*|tools?|steel"
    r"|buildings?|roads?|cars?|vehicles?|landscapes?|scenes?|locations?|backdrops?|environments?"
    r"|weather|practical|motivated|across frames"
    # 氛围和时段光：定妆照的灰背景上会画出烟雾、暖色偏色（第二次样片的「atmospheric haze」）
    r"|haze|hazy|smok\w*|dust\w*|steam\w*|atmospher\w*|backlit|rim light|golden.hour|sidelight"
    r"|sunlit)\b", re.I)
# 画风 / 媒介（动漫、三渲二、插画、漫画、水彩……）：不是写实时这句最要紧，不能被当成非成像词丢掉
MEDIUM_TERMS = re.compile(
    r"\b(?:anime|manga|manhua|cel[- ]?shad\w*|cel|illustrat\w*|cartoon\w*|painted|painting|painterly"
    r"|3d|cgi|cg|render\w*|line[- ]art|comic\w*|ink|watercolou?r|gouache|pixar|chibi|stylized"
    r"|stylised|2d|animation|animated|claymation|pixel[- ]art|outlines?|linework|flat colou?rs?"
    r"|shading|photo\w*|realis\w*|live-action)\b",
    re.I)
# 句中不该改成小写的专有名词（胶片、相机、镜头品牌）
_PROPER = re.compile(r"(?:Kodak|Fuji|Fujifilm|ARRI|Alexa|RED|Cooke|Zeiss|Leica|Canon|Sony|Panavision"
                     r"|Ektachrome|Portra|CineStill|Cinestill|Vision3|Ilford|Pixar|Ghibli)\b")


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
    keep = [c for c in _clauses(anchor)
            if (_PHOTO_TERMS.search(c) or MEDIUM_TERMS.search(c)) and not _SCENE_TERMS.search(c)]
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
        if kept == re.split(r",\s*", body):   # 这一句没动：原样保留
            out.append(sentence)
        elif kept:
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
# 加词边界：open-face（头盔）、surface、interface 不算写脸
_FACE = re.compile(r"(?<![\w-])faces?\b|\bhair\w*|\beyes?\b|\beyebrows?\b|\bbrows?\b|\bnose\b|\blips?\b"
                   r"|\bmouth\b|\bjaw\w*|\bcheek\w*|\bchin\b|\bskin\b|\bcomplexion\b|\bbeard\b|\bstubble\b"
                   r"|\bmustache\b|\bmoustache\b|\bglasses\b|\bspectacles\b|\bearrings?\b|\bfreckles?\b"
                   r"|\bmoles?\b|\bscars?\b|\bwrinkle\w*|\bforehead\b|\bbangs\b|\bponytail\b|\bbun\b"
                   r"|\bbraids?\b", re.I)
_TOKEN_WITH_TAIL = re.compile(CHAR_TOKEN.pattern + r"(\s*[.,;:!?])?")
FACE_SIZES = ("大特写", "特写")
UPPER_SIZES = ("近景",)


def char_token(char_id: int) -> str:
    return "{{char:%d}}" % char_id


_ACCESSORY = re.compile(r"\b(?:bags?|backpacks?|purses?|handbags?|watch|wristwatch|bracelets?|belts?"
                        r"|helmets?|umbrellas?|phones?|briefcases?)\b", re.I)


def face_look(visual_prompt: str) -> str:
    """特写用的外貌：第一句（性别、年龄、族裔）加上写脸、头发、皮肤、眼镜这些的句子。
    外貌只有一句时，在句子里去掉下半身、手、随身物件这些分句（特写里看不见）。"""
    sentences = [x for x in re.split(r"(?<=[.!?])\s+", (visual_prompt or "").strip()) if x]
    if not sentences:
        return ""
    if len(sentences) == 1:
        drop = re.compile(f"{_LOWER_BODY.pattern}|{_HAND_DETAIL.pattern}|{_ACCESSORY.pattern}", re.I)
        return _strip_clauses(sentences[0], drop, _FACE) or sentences[0]
    kept = [sentences[0]] + [x for x in sentences[1:] if _FACE.search(x)]
    return " ".join(kept)


def look_for_size(visual_prompt: str, shot_size: str = "") -> str:
    """按景别取舍外貌：大特写、特写只要脸和头发；近景去掉只写下半身的句子；中景、全景用全文。"""
    if shot_size in FACE_SIZES:
        return face_look(visual_prompt)
    if shot_size in UPPER_SIZES:
        return upper_body_look(visual_prompt)
    return (visual_prompt or "").strip()


# 填好外貌后整段提示词的上限（词）：Z-Image（Qwen3-4B）/ krea2（T5）编码器约 512 token；
# 260 词 + 风格锚约 70 词 ≈ 430 token，放得下。以前是 200：两人同框的中景必超，退成只写脸、服装全丢
MAX_RESOLVED_WORDS = 260

_SENTENCES = re.compile(r"(?<=[.!?])\s+")
_WEARS = re.compile(r"^\s*(?:wears|wearing|dressed|clad|in an?\b)", re.I)
_CLOTHES = re.compile(r"jacket|shirt|t-shirt|coat|sweater|hoodie|blouse|vest|cardigan|uniform|dress\b"
                      r"|apron|suit\b|robe|gown|overalls|pinafore|windbreaker|polo|tee\b|top\b", re.I)


def _sentences(text: str) -> list[str]:
    return [x for x in _SENTENCES.split((text or "").strip()) if x]


def costume_sentence(visual_prompt: str, upper: bool = True) -> str:
    """外貌里写服装的那一句（「Wears …」优先，没有就第一句写了衣服的），upper=True 时去掉下半身。"""
    rest = _sentences(visual_prompt)[1:]
    pick = next((x for x in rest if _WEARS.search(x)), None) or next(
        (x for x in rest if _CLOTHES.search(x) and not x.lower().startswith("signature")), "")
    if not pick:
        return ""
    return (upper_body_look(pick) if upper else pick).strip()


def signature_sentence(visual_prompt: str) -> str:
    """「Signature detail: …」（角色的标志性细节，core feature）：去掉下半身后主语还在才用。"""
    sig = next((x for x in _sentences(visual_prompt) if x.lower().startswith("signature")), "")
    if not sig:
        return ""
    cut = upper_body_look(sig).strip()
    return cut if cut.lower().startswith("signature") else ""


def compact_look(visual_prompt: str, with_signature: bool = True) -> str:
    """压缩版外貌：身份句（性别年龄、脸、发型、体型）+ 标志性细节（core feature）+ 服装句（上半身）。
    每个同框的人都留服装，首帧只靠文字的引擎（zimage / krea2）才不会换装。"""
    sents = _sentences(visual_prompt)
    if not sents:
        return ""
    parts = [sents[0]]
    sig = signature_sentence(visual_prompt) if with_signature else ""
    wear = costume_sentence(visual_prompt)
    parts += [x for x in (sig, wear) if x]
    return " ".join(parts)


# ------------------------------------------------------------ 插入镜头：只露手 / 身体局部 / 物件
_HANDS_SUBJ = re.compile(r"\b(?:fingers?|fingertips?|hands?|palms?|knuckles?|wrists?|thumbs?)\b", re.I)
_BODY_SUBJ = re.compile(r"\b(?:hips?|waist|pocket|belt|torso|back of|lap|knees?|feet|foot|shoes?)\b", re.I)
_FACE_SUBJ = re.compile(r"(?<![\w-])faces?\b|\beyes?\b|\blips?\b|\bmouth\b|\btears?\b|\bgaze\b"
                        r"|\bcheeks?\b|\bbrows?\b|\bexpression\b|\bsmil\w*|\bhead\b|\bhair\b", re.I)


def insert_kind(image_prompt: str, shot_size: str = "") -> str:
    """特写 / 大特写里画的是不是「插入镜头」：hands = 只露手，body = 只露腰、口袋这类身体局部，
    object = 没有占位符（调用方只在 cast 为空时把它当物件镜头；老镜头没写占位符也会得到 object）；
    拍到脸的特写返回空串。看每个外貌占位符后面那一句（写这个人的句子）。"""
    if shot_size not in FACE_SIZES:
        return ""
    text = image_prompt or ""
    ends = [m.end() for m in CHAR_TOKEN.finditer(text)]
    if not ends:
        return "object"
    kinds = set()
    for end in ends:
        rest = CHAR_TOKEN.sub("", text[end:]).lstrip(" .,;")
        sentence = (_sentences(rest) or [""])[0]
        if _FACE_SUBJ.search(sentence):
            return ""
        if _HANDS_SUBJ.search(sentence):
            kinds.add("hands")
        elif _BODY_SUBJ.search(sentence):
            kinds.add("body")
        else:
            return ""
    return "hands" if kinds == {"hands"} else "body"


def insert_look(visual_prompt: str, kind: str) -> str:
    """插入镜头里这个人的外貌：只写看得见的服装（手 → 上衣、袖子；身体局部 → 整句服装），不写脸和头发。"""
    return costume_sentence(visual_prompt, upper=(kind == "hands"))


def resolve_char_tokens(text: str, looks: dict[int, str], shot_size: str = "",
                        notes: list | None = None) -> str:
    """把 {{char:<id>}} 换成该角色按景别取舍后的外貌（句末补句号）；没有占位符时原样返回。
    找不到的角色（删了）或外貌是空的、或是中文的，占位符直接去掉（中文进英文提示词会被画成字）。
    - 只露手 / 身体局部的插入镜头（insert_kind）：只填看得见的服装，不填脸和头发；
    - 同框两人及以上时每人只用上半身外貌；
    - 填完超过 MAX_RESOLVED_WORDS 个词：每人退到「身份句 + 标志性细节 + 服装句」，还超就去掉标志性细节，
      再超才只写脸；notes 里记一句（任务日志用）。"""
    if not text or "{{" not in text:
        return text or ""
    kind = insert_kind(text, shot_size)
    if kind in ("hands", "body"):
        return _resolve(text, looks, lambda look: insert_look(look, kind))
    people = {int(x) for x in CHAR_TOKEN.findall(text)}
    multi = len(people) >= 2 and shot_size not in FACE_SIZES
    out = _resolve(text, looks, lambda look: upper_body_look(look) if multi
                   else look_for_size(look, shot_size))
    if len(out.split()) <= MAX_RESOLVED_WORDS or shot_size in FACE_SIZES:
        return out
    steps = ((compact_look, "每人只留身份、标志性细节和服装"),
             (lambda look: compact_look(look, with_signature=False), "每人只留身份和服装"),
             (face_look, "外貌改成只写脸"))
    for pick, note in steps:
        out = _resolve(text, looks, pick)
        if len(out.split()) <= MAX_RESOLVED_WORDS or pick is face_look:
            if notes is not None:
                notes.append(f"填好外貌后超过 {MAX_RESOLVED_WORDS} 个词，{note}")
            break
    return out


def _resolve(text: str, looks: dict[int, str], pick) -> str:
    def sub(m: re.Match) -> str:
        look = pick(looks.get(int(m.group(1)), "") or "").strip()
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


def shot_image_prompt(shot, project, notes: list | None = None) -> str:
    """镜头的 image_prompt，占位符换成角色当前的外貌（按这一镜的景别取舍）。"""
    text = shot.image_prompt or ""
    if "{{" not in text:
        return text
    return resolve_char_tokens(text, project_looks(project), shot.shot_size or "", notes)


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
    """视频提示词里怎么称呼说话人：英文简称（不写中文名；画面里的人用分镜里写的同一个简称），
    他在不在画面里（在这一镜的 cast 里），以及性别（male / female / 空），画外音要说清是男声还是女声。"""
    ref: str
    on_screen: bool
    gender: str = ""


_FEMALE = re.compile(r"\b(?:woman|women|girl|lady|female|mother|grandmother|wife|sister|daughter|aunt"
                     r"|actress)\b", re.I)
_MALE = re.compile(r"\b(?:man|men|boy|gentleman|male|father|grandfather|husband|brother|son|uncle)\b",
                   re.I)


def gender_of(visual_prompt: str | None) -> str:
    """从外貌的第一句看性别：female / male；看不出来返回空串。"""
    head = (_sentences(visual_prompt or "") or [""])[0]
    f, m = _FEMALE.search(head), _MALE.search(head)
    if f and (not m or f.start() <= m.start()):
        return "female"
    if m:
        return "male"
    return ""


_PERSON_NOUN = (r"(?:woman|man|girl|boy|lady|gentleman|teenager|teen|child|kid|guy|mother|father"
                r"|grandmother|grandfather|grandma|grandpa|worker|courier|driver|stranger)")
_TOKEN_REF = re.compile(CHAR_TOKEN.pattern + r"\s*[.,;:]?\s*(?:The|the)\s+((?:[\w-]+\s+){0,3}?"
                        + _PERSON_NOUN + r")\b")


def token_refs(image_prompt: str | None) -> dict[int, str]:
    """分镜给每个人的简短指代：image_prompt 里占位符后面紧跟的「The young woman …」。
    视频对白里说话人用同一个叫法，H3 才知道动作和台词说的是同一个人（双人镜头谁张嘴）。"""
    out: dict[int, str] = {}
    for m in _TOKEN_REF.finditer(image_prompt or ""):
        out.setdefault(int(m.group(1)), "the " + " ".join(m.group(2).split()).lower())
    return out


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
        return f"{sp.ref[:1].upper()}{sp.ref[1:]} {said}"
    voice = f"An off-screen {sp.gender} voice" if sp is not None and sp.gender else "An off-screen voice"
    if sp is not None and sp.ref and sp.ref != "the speaker":
        voice += f" ({sp.ref})"
    lips = ", the on-screen character's lips stay closed." if anyone_on_screen else ""
    return f"{voice} {said}{lips}"


def _join_lines(lines: list[str]) -> str:
    """多句台词按先后写成独立的句子：第二句起加 Then，句子之间不会粘在一起。"""
    return " ".join(line if i == 0 else "Then " + line[:1].lower() + line[1:]
                    for i, line in enumerate(lines))


GAZE_LINE = ("The people on screen keep their eyes on each other and on what they are doing, "
             "looking away from the camera.")
GAZE_ONE = ("The person on screen keeps their eyes on what they are doing, looking away from the "
            "camera.")
_AT_CAMERA = re.compile(r"\b(?:into|at|toward|towards|to)\s+(?:the\s+)?(?:camera|lens|viewer)\b", re.I)


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
        # 第二次样片 S03：motion 写 glancing up，H3 理解成看镜头。画面里有人、剧本没让他看镜头时写明视线
        n_on = sum(1 for sp in speakers.values() if sp.on_screen)
        if n_on and not _AT_CAMERA.search(body):
            text = f"{text} {GAZE_ONE if n_on == 1 else GAZE_LINE}"
    if not has_audio:
        return text
    audio = (audio_prompt or "").strip()
    anyone = any(sp.on_screen for sp in (speakers or {}).values())
    lines = [_dialogue_line(d, speakers, anyone)
             for d in dialogue or [] if d.get("zh") and d["zh"] not in audio]
    if speakers is not None:   # 发给引擎：只要画面里的声音，音乐交给成片配乐（不进输入哈希）
        audio = diegetic_audio(audio)
    joined = " ".join(lines) if speakers is None else _join_lines(lines)
    parts = [p for p in (audio, joined) if p]
    if speakers is not None:
        parts.append(NO_MUSIC)
    return text + ("\nAudio: " + " ".join(parts) if parts else "")


# 视频连续性：H3 带参考图时曾在镜头中途切到定妆照（灰色影棚背景、正面站立）。只拼进发给引擎的提示词，
# 不进输入哈希和 inputs 摘要——否则模板一改，所有已出的视频都会被判过期。
# 用正面写法点名这一镜的地点：「no studio background」这类否定词反而会把影棚背景招进画面。
_CJK_ANY = re.compile(r"[⺀-鿿가-힯豈-﫿＀-￯]")


# 场景外观第一句的开头：这些常见词可以放心改小写、补冠词；不在表里的（可能是地名、店名）不动大小写，
# 改用「this location: …」接，不猜冠词
_LOC_COMMON = frozenset((
    "a an the this one narrow small large big tiny old dim dark bright cramped crowded quiet busy "
    "empty cluttered run-down rundown shabby modern cozy cosy spacious tidy messy worn rustic "
    "traditional simple long wide rainy abandoned deserted roadside suburban rural local cobbled "
    "tiled wooden weathered dusty humble low-ceilinged open-air outdoor indoor tight compact "
    "corner room shop street alley kitchen bedroom living office corridor hallway courtyard rooftop "
    "studio garage workshop cafe restaurant classroom hospital market park bus train car apartment "
    "house hotel bar lobby stairwell warehouse field beach riverside bridge temple back front "
    "repair stall booth counter village town lane").split())
_LOC_NO_ARTICLE = frozenset(("two three four five six seven eight nine ten several many some "
                             "rows stacks piles shelves lines dozens racks").split())
_LOC_LEAD = re.compile(r"^(?:inside|interior of|in|at|within)\s+", re.I)


def location_summary(visual_prompt: str | None, max_words: int = 30) -> str:
    """场景外观的第一句（最多 max_words 个词），拼进「inside …」；有中文就不用。

    去掉开头的 inside / interior of / in / at；第一个词是常见词时改小写、没冠词就补 a / an
    （「Narrow suburban …」→「a narrow suburban …」）；复数和数词开头不补冠词
    （「Rows of rusty bicycles」→「rows of rusty bicycles」）；其他（可能是地名，「Shanghai alley」）
    不改大小写，写成「this location: Shanghai alley」。"""
    text = (visual_prompt or "").strip()
    if not text or _CJK_ANY.search(text):
        return ""
    first = re.split(r"(?<=[.!?])\s+", text)[0].rstrip(" .!?;")
    first = _LOC_LEAD.sub("", first).strip()
    if not first:
        return ""
    head = first.split()[0]
    low = head.lower().strip(",;:")
    if re.match(r"\d", head) or low in _LOC_NO_ARTICLE:
        first = first[:1].lower() + first[1:] if low in _LOC_NO_ARTICLE else first
    elif low in _LOC_COMMON and not head[1:2].isupper():
        first = first[:1].lower() + first[1:]
        if low not in ("a", "an", "the", "this", "one"):
            first = ("an " if low[0] in "aeiou" else "a ") + first
    else:
        first = "this location: " + first
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


def with_continuity(video_prompt: str, location: str = "", extra: list[str] | tuple = ()) -> str:
    """把连续性要求接在画面描述后面、Audio 段前面。location 是 location_summary 的结果；
    extra 是另外要接在画面描述后面的句子（新入画角色的外貌、结尾留在画面里）。"""
    body, sep, audio = (video_prompt or "").partition("\nAudio:")
    body = " ".join([body.rstrip(), *[x for x in extra if x], continuity_clause(location)])
    return body.strip() + (sep + audio if sep else "")


ENTERING_WORDS = 60   # 身份句 + 服装句，按句累加


def entering_clause(visual_prompts: list[str]) -> str:
    """接上一镜：首帧（上一镜的尾帧）里还没有、这一镜才从画外进来的角色，写一句简短外貌（上半身），
    和一起带上的定妆照对上——第三次样片 S06 的苏晴没有这句，H3 给她换了白衬衫、长直发。"""
    looks = []
    for vp in visual_prompts:
        if not vp or _CJK_ANY.search(vp):
            continue
        # 按句累加到 ENTERING_WORDS 个词为止，不在词中间截断（以前截出「over a plain.」）；
        # 一句都放不下时退到第一句
        kept: list[str] = []
        for x in _sentences(compact_look(vp, with_signature=False)):
            if kept and len(" ".join(kept + [x]).split()) > ENTERING_WORDS:
                break
            kept.append(x)
        if kept:
            looks.append(" ".join(kept).rstrip(".,; "))
    return ("Entering the frame: " + "; ".join(looks) + ".") if looks else ""


# 第三次样片 S06：结尾镜头里的人最后走出了画面，「爸在屋里」的压迫感没了
STAY_IN_FRAME = ("Everyone on screen stays inside the frame until the very last moment of the shot; "
                 "nobody walks out of frame.")

# 第三次样片：分镜 audio_prompt 写了钢琴、弦乐，H3 自己配了一层音乐，和成片配乐叠在一起，持续音高还让
# 浊音检测把整条判成「在说话」。发给引擎时只留画面里真实存在的声音，结尾写明不要配乐。
# 只认「配乐式」写法（非画面内的音乐）：形容词 + 钢琴 / 弦乐 / 音乐、background / ambient music、score、
# soundtrack、bgm、music swells / plays / fades、no music。光出现 piano、string、musical 不算——
# 「a piano lid slams shut」「a string of fairy lights」「musical instrument shop」都是画面里的声音。
_INSTR = r"(?:piano|strings?(?!\s+of\b)|music|cello|violins?|orchestra)"
_MUSIC = re.compile(
    r"\b(?:soft|gentle|tense|melancholic|swelling|low|sad|warm|emotional|haunting|eerie|ominous"
    r"|sustained|faint|subtle|quiet|single)\b(?:[\s-]+[a-z]+){0,3}?[\s-]+" + _INSTR + r"\b"
    r"|\b(?:background|ambient|underscore)\s+music\b|\bscore\b|\bsoundtrack\b|\bbgm\b"
    r"|\bmusic\s+(?:swells?|plays?|fades?|rises?|builds?|starts?|kicks?\s+in)\b"
    r"|\b(?:no|without)\s+(?:background\s+)?music\b", re.I)
# 有人或物在画面里发出动作的分句保留（街头艺人弹吉他、琴盖砸下）
_PHYSICAL = re.compile(r"\b(?:slams?|strums?|plucks?|taps?|hits?|knocks?|tugs?|bangs?|drops?|"
                       r"lifts?|opens?|closes?|rustles?|tunes?|presses?)\b", re.I)
NO_MUSIC = "No background music."


def _is_score(clause: str) -> bool:
    return bool(_MUSIC.search(clause)) and not _PHYSICAL.search(clause)


def diegetic_audio(audio: str) -> str:
    """去掉写音乐的分句（按句子、逗号切；「no music」这类也去掉，compose_video_prompt 最后统一接 NO_MUSIC）。"""
    kept: list[str] = []
    for sentence in re.split(r"(?<=[.;!?])\s+", (audio or "").strip()):
        sentence = sentence.strip()
        if not sentence:
            continue
        end = sentence[-1] if sentence[-1] in ".!?" else "."
        parts = [p.strip() for p in sentence.rstrip(".;!? ").split(",") if p.strip()]
        parts = [p for p in parts if not _is_score(p)]
        if parts:
            text = ", ".join(parts)
            kept.append(text[:1].upper() + text[1:] + end)
    return " ".join(kept)


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
    """选定的首帧是不是带定妆照锁过脸。首帧候选的 inputs["refs"] 记着引擎实际用到的参考图，
    其中有定妆照或三视图格才算锁脸（只带了场景图 / 构图参考的不算）；
    老候选没有 inputs 时按出图引擎能不能吃参考图估计（引擎不存在、上传的首帧按没锁脸算）。

    没锁脸（zimage / krea2 文生图）时，首帧里的脸是文字画出来的，和定妆照不是同一张脸：
    视频再带定妆照，H3 会在两张脸之间折中或中途「跳」到参考图。这种镜头视频身份以首帧为准。"""
    inputs = (ff_params or {}).get("inputs")
    if isinstance(inputs, dict) and "refs" in inputs:
        return any(_is_face_ref(r) for r in inputs["refs"] or [])
    return image_max_refs > 0


def _is_face_ref(r) -> bool:
    """首帧用到的这张参考图带不带脸（定妆照 / 三视图格）。场景图、构图参考不算锁脸。
    新记录有 kind；老记录只有 label，按「XX 定妆照」「XX 三视图·正面」认。"""
    if not isinstance(r, dict):
        return False
    kind = r.get("kind")
    if kind:
        return kind in ("portrait", "turnaround")
    label = str(r.get("label") or "")
    return label.endswith("定妆照") or "三视图·" in label


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
    return [{"id": r.candidate_id, "label": r.label, **({"kind": r.kind} if getattr(r, "kind", "") else {})}
            for r in refs]


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
    old：候选 params["inputs"]（老候选没有）；new：按镜头当前状态算的同结构摘要。
    接上一镜的首帧（pipeline/continuity.py）摘要里有 prev_video：按上一镜的视频比对。"""
    o = old if isinstance(old, dict) else {}
    if "prev_video" in new or "prev_video" in o:
        if "prev_video" not in new:
            return "关掉了「接上一镜」"
        if not new["prev_video"]:
            return f"上一镜 {new.get('prev_shot') or ''} 还没有选定视频".replace("  ", " ")
        if "prev_video" not in o:
            return "改成了接上一镜"
        if o["prev_video"] != new["prev_video"]:
            return f"上一镜 {new.get('prev_shot')} 的视频换了"
        return "输入已变化"
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
