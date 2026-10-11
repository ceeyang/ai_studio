"""角色三视图：按行业通行的角色设定图（character turnaround / model sheet）做。

一张图里同一个人并排出现几次：全身设定图 4 个视角（正面 / 3/4 侧 / 90° 侧面 / 背面，同比例、
中性站姿、正交机位、纯色背景、平光），头像设定图 3 个视角（正脸 / 3/4 / 侧脸）。一张图一起出比
逐格出一致得多，侧面也才是真的侧面。版式是固定模板（下面的 BODY_TEMPLATE / FACE_TEMPLATE），
LLM 只负责把外貌提示词整理成一段从头到脚的"全身描述"（Character.turnaround_look，可改）。

槽位：设定图 Slot(kind="turnaround_sheet_<sheet>")，候选可多张、可切换；选定的那张自动按人物
切成单个视角，存进 Slot(kind="turnaround_view_<view>")，首帧 / 视频打包参考图时按镜头的景别和
机位挑最匹配的一格（view_for_shot）。格子的过期跟着它切自的那张设定图走。
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass

from PIL import Image
from sqlalchemy.orm import Session

from backend.domain.characters import portrait_of
from backend.domain.hashing import input_hash
from backend.domain.models import Candidate, Character, Slot
from backend.domain.sheet_crop import crop_sheet
from backend.domain.slots import add_candidate, get_or_create_slot, selected_candidate


@dataclass(frozen=True)
class SheetSpec:
    label: str
    aspect: str
    views: tuple[str, ...]


SHEETS: dict[str, SheetSpec] = {
    "body": SheetSpec("全身三视图", "21:9", ("front_full", "three_quarter", "profile", "back")),
    # 头像也用 21:9：16:9 时 H3 会照着定妆照的中景构图出，3/4 转不过去；宽画幅三个头之间留得开
    "face": SheetSpec("头像", "21:9", ("face_front", "face_three_quarter", "face_profile")),
}
SHEET_KEYS: tuple[str, ...] = tuple(SHEETS)

VIEWS: tuple[tuple[str, str], ...] = (
    ("front_full", "正面全身"),
    ("three_quarter", "3/4 侧"),
    ("profile", "侧面"),
    ("back", "背面"),
    ("face_front", "正脸"),
    ("face_three_quarter", "3/4 脸"),
    ("face_profile", "侧脸"),
)
VIEW_KEYS: tuple[str, ...] = tuple(k for k, _ in VIEWS)
VIEW_LABELS: dict[str, str] = dict(VIEWS)
VIEW_SHEET: dict[str, str] = {v: k for k, spec in SHEETS.items() for v in spec.views}

# 默认用 H3 出设定图：带定妆照当参考图时脸和定妆照是同一个人，4 个视角的顺序也最听话。
# 没有 H3 时退到项目的图像引擎。
DEFAULT_SHEET_ENGINE = "h3_image"

# ---------------------------------------------------------------- 设定图提示词模板（固定）
BODY_TEMPLATE = (
    "Professional character turnaround sheet, photographic model sheet: the same person "
    "photographed four times and placed side by side in one horizontal row, exactly four full-body "
    "figures with wide empty space between them. Figure 1, far left: front view, facing the camera "
    "straight on. Figure 2: three-quarter view, body and face turned 45 degrees toward the right "
    "side of the image, both eyes still visible, the near shoulder closer to the camera. Figure 3: "
    "side profile view, body and head turned 90 degrees to face the right edge of the image, only "
    "one eye and one ear visible, shoulders perpendicular to the camera. Figure 4, far right: back "
    "view, facing away from the camera, the back of the head and the back of the outfit visible. "
    "Character: {description} All four figures are the identical person with the "
    "identical face, hairstyle, outfit, colors and accessories. Every figure is shown in full from "
    "the top of the head to the soles of the shoes, all four the same height and the same scale, "
    "standing on one shared ground line. The figures are small in the frame, each about 80 percent of "
    "the image height, with clear empty background above the heads and below the shoes and a wide "
    "margin at the image edges. Neutral relaxed standing pose, feet together, arms hanging "
    "relaxed at the sides, calm neutral expression. Orthographic camera at chest height with a long "
    "telephoto lens, true-to-scale proportions, every figure in sharp focus. Plain seamless pure white "
    "studio background, one continuous seamless backdrop, flat even soft studio lighting "
    "identical across all four figures, faint soft shadow under the feet. "
    "{style} Crisp detail on the face, hair and fabric. Only the four figures of this one person on "
    "the clean white background, empty hands, nothing else in the image."
)
FACE_TEMPLATE = (
    "Professional character head turnaround sheet, photographic headshot reference: the same person "
    "photographed three times and placed side by side in one horizontal row, exactly three head and "
    "shoulders close-up portraits separated by wide empty gaps. Portrait 1, far left: front view, face "
    "looking straight into the camera, both ears visible. Portrait 2, center: three-quarter view, "
    "halfway between the front view and the side profile: head and shoulders turned 45 degrees toward "
    "the right side of the image, the far cheek partly hidden, the far ear hidden, eyes looking the "
    "same direction as the nose. Portrait 3, far right: side profile, head "
    "and shoulders turned 90 degrees to face the right edge of the image, only one eye and one ear "
    "visible. Character: {description} All three portraits are the identical person with the "
    "identical face, skin, hairstyle and clothing. Close-up framing: each portrait shows only the head, "
    "neck and the top of the shoulders, cut off at the collarbones; all "
    "three heads the same size and at the same height, with plain background above every head and "
    "between the portraits. Calm "
    "neutral expression, relaxed closed mouth. Orthographic camera at eye level with a long "
    "telephoto lens, true-to-scale proportions, every face in sharp focus. Plain seamless pure white "
    "studio background, one continuous seamless backdrop, flat even soft studio lighting identical "
    "across all three portraits. {style} Crisp detail on the skin, eyes and hair. Only the three "
    "portraits of this one person on the clean white background, nothing else in the image."
)
TEMPLATES = {"body": BODY_TEMPLATE, "face": FACE_TEMPLATE}
# 以前的模板（否定句写法）：老设定图的输入哈希按它算，换了模板不判过期（过期的格子不进参考图，
# 会让所有带三视图的首帧、视频一起过期重出）
_V1_BODY_TEMPLATE = (
    "Professional character turnaround sheet, photographic model sheet: the same person "
    "photographed four times and placed side by side in one horizontal row, exactly four full-body "
    "figures with wide empty space between them. Figure 1, far left: front view, facing the camera "
    "straight on. Figure 2: three-quarter view, body and face turned 45 degrees toward the right "
    "side of the image, both eyes still visible, the near shoulder closer to the camera. Figure 3: "
    "side profile view, body and head turned 90 degrees to face the right edge of the image, only "
    "one eye and one ear visible, shoulders perpendicular to the camera. Figure 4, far right: back "
    "view, facing away from the camera, the back of the head and the back of the outfit visible, "
    "face not visible. Character: {description} All four figures are the identical person with the "
    "identical face, hairstyle, outfit, colors and accessories. Every figure is shown in full from "
    "the top of the head to the soles of the shoes, all four the same height and the same scale, "
    "standing on one shared ground line. The figures are small in the frame, each about 80 percent of "
    "the image height, with clear empty background above the heads and below the shoes, nothing "
    "touching the image edges. Neutral relaxed standing pose, feet together, arms hanging "
    "relaxed at the sides, calm neutral expression. Orthographic camera at chest height with a long "
    "telephoto lens, no perspective distortion. Plain seamless pure white studio background, one "
    "continuous backdrop without panel borders or dividing lines, flat even soft studio lighting "
    "identical across all four figures, faint soft shadow under the feet. "
    "{style} Crisp detail on the face, hair and fabric. No text, no labels, no numbers, no props, "
    "no other people."
)
_V1_FACE_TEMPLATE = (
    "Professional character head turnaround sheet, photographic headshot reference: the same person "
    "photographed three times and placed side by side in one horizontal row, exactly three head and "
    "shoulders close-up portraits separated by wide empty gaps. Portrait 1, far left: front view, face "
    "looking straight into the camera, both ears visible. Portrait 2, center: three-quarter view, "
    "halfway between the front view and the side profile: head and shoulders turned 45 degrees toward "
    "the right side of the image, the far cheek partly hidden, the far ear hidden, eyes looking the "
    "same direction as the nose, not at the camera. Portrait 3, far right: side profile, head "
    "and shoulders turned 90 degrees to face the right edge of the image, only one eye and one ear "
    "visible. Character: {description} All three portraits are the identical person with the "
    "identical face, skin, hairstyle and clothing. Close-up framing: each portrait shows only the head, "
    "neck and the top of the shoulders, cut off at the collarbones, the chest out of frame; all "
    "three heads the same size and at the same height, with plain background above every head and "
    "between the portraits. Calm "
    "neutral expression, mouth closed, no smile. Orthographic camera at eye level with a long "
    "telephoto lens, no perspective distortion. Plain seamless pure white studio background, one "
    "continuous backdrop without panel borders, flat even soft studio lighting identical across all "
    "three portraits. {style} Crisp detail on the skin, eyes and hair. No text, no labels, no "
    "numbers, no props, no other people."
)
_V1_TEMPLATES = {"body": _V1_BODY_TEMPLATE, "face": _V1_FACE_TEMPLATE}
SHEET_NEGATIVE = ("background scenery, room, floor clutter, props, text, labels, numbers, "
                  "watermark, cropped head, cropped feet, overlapping figures, different outfits, "
                  "different people, extra person")
DEFAULT_STYLE = "Photorealistic live-action photography, everything in sharp focus."
# 设定图要正交、整张清晰：风格锚里的浅景深、虚化、变形宽银幕、光晕、焦段、剧情类型词都不要
_SHEET_DROP = re.compile(r"depth of field|bokeh|anamorphic|halation|\blens\b|\d+\s?mm\b|focus|blur"
                         r"|\bshot on\b", re.I)
# 只去掉这几个词、保留分句（「Photorealistic live-action cinematic style」→「Photorealistic live-action style」）
_SHEET_WORDS = re.compile(r"\s*\b(?:cinematic|drama|still)\b", re.I)
STYLE_WORDS = 25


def style_line(anchor: str) -> str:
    """设定图用的风格句：风格锚里只和成像有关的分句（photo_style，去掉场景、天气、光源），再去掉
    浅景深、变形镜头、光晕、焦段这类和「正交、整张清晰」矛盾的分句和 cinematic / drama 这类词，
    没有画风（写实 / 动漫）时补上默认的写实，最后写明全部清晰。"""
    # 包 backend.pipeline 会反过来导入本模块
    from backend.pipeline.prompting import MEDIUM_TERMS, photo_style

    kept: list[str] = []
    for clause in (_SHEET_WORDS.sub("", c).strip() for c in photo_style(anchor).split(",")):
        if clause and not _SHEET_DROP.search(clause) \
                and len(" ".join(kept + [clause]).split()) <= STYLE_WORDS:
            kept.append(clause)
    if not kept:
        return DEFAULT_STYLE
    if not any(MEDIUM_TERMS.search(c) for c in kept):   # 锚里写了画风（动漫、三维、漫画…）就不补写实
        kept.insert(0, "Photorealistic live-action photography")
    text = ", ".join(kept).rstrip(" .")
    return text[:1].upper() + text[1:] + ", everything in sharp focus."


def _v1_style_line(anchor: str) -> str:
    first = re.split(r"(?<=[.!?。])\s+", (anchor or "").strip(), maxsplit=1)[0].strip()
    if not first:
        return "Photorealistic live-action photography."
    return first if first[-1] in ".!?。" else first + "."


def sheet_prompt(sheet: str, description: str, anchor: str) -> str:
    return TEMPLATES[sheet].format(description=description.strip(), style=style_line(anchor))


def sheet_slot_kind(sheet: str) -> str:
    return f"turnaround_sheet_{sheet}"


def view_slot_kind(view: str) -> str:
    return f"turnaround_view_{view}"


def uses_refs(engine_name: str) -> bool:
    from backend.engines.registry import ENGINE_CLASSES

    cls = ENGINE_CLASSES.get(engine_name)
    return bool(cls and cls.caps.max_refs > 0)


def look_of(char: Character) -> str:
    """出设定图用的全身描述：没写就用外貌提示词。"""
    return (char.turnaround_look or "").strip() or (char.visual_prompt or "").strip()


def sheet_inputs(char: Character, sheet: str) -> tuple[str, str]:
    """(prompt, negative)。"""
    from backend.pipeline.prompting import join_negative  # 包 backend.pipeline 会反过来导入本模块

    proj = char.project
    style = proj.style_bible
    prompt = sheet_prompt(sheet, look_of(char), style.anchor_text if style else "")
    negative = join_negative(proj.negative, style.negative if style else "", SHEET_NEGATIVE)
    return prompt, negative


def sheet_hash(sheet: str, look: str, anchor: str, negative: str, engine_name: str,
               portrait_id: int | None) -> str:
    """只算数据，不含种子和模板文字：重出输入不变；改全身描述 / 风格锚 / 换定妆照（引擎吃参考图时）
    才算过期。模板文字改了不判过期（和剧本、分镜的规则版本一个道理）。"""
    return input_hash("turnaround_sheet_inputs", sheet, (look or "").strip(), anchor or "",
                      negative, engine_name, portrait_id if uses_refs(engine_name) else None)


def legacy_sheet_hash(sheet: str, look: str, anchor: str, negative: str, engine_name: str,
                      portrait_id: int | None) -> str:
    """以前的算法：整段提示词（老模板）的哈希。老设定图按它比，不因为换了模板就过期。"""
    prompt = _V1_TEMPLATES[sheet].format(description=(look or "").strip(),
                                         style=_v1_style_line(anchor))
    return input_hash("turnaround_sheet", sheet, prompt, negative, engine_name,
                      portrait_id if uses_refs(engine_name) else None)


def current_sheet_hashes(s: Session, char: Character, sheet: str, engine_name: str) -> set[str]:
    _, negative = sheet_inputs(char, sheet)
    style = char.project.style_bible
    anchor = style.anchor_text if style else ""
    p = portrait_of(s, char.id)
    pid = p.id if p else None
    look = look_of(char)
    return {sheet_hash(sheet, look, anchor, negative, engine_name, pid),
            legacy_sheet_hash(sheet, look, anchor, negative, engine_name, pid)}


def cell_hash(sheet_hash_: str, view: str) -> str:
    return input_hash("turnaround_cell", sheet_hash_, view)


def _sheet_stale(s: Session, char: Character, sheet: str, cand: Candidate | None) -> bool:
    return cand is None or cand.input_hash not in current_sheet_hashes(s, char, sheet, cand.engine)


def sheet_state(s: Session, char: Character, sheet: str) -> tuple[Candidate | None, bool]:
    """(选定的设定图, 是否过期)。没出过不算过期。"""
    sel = selected_candidate(s, "character", char.id, sheet_slot_kind(sheet))
    if sel is None:
        return None, False
    return sel, _sheet_stale(s, char, sheet, sel)


def cell_state(s: Session, char: Character, view: str) -> tuple[Candidate | None, bool]:
    """(选定的格子, 是否过期)：切自的那张设定图过期了，格子就过期。"""
    sel = selected_candidate(s, "character", char.id, view_slot_kind(view))
    if sel is None:
        return None, False
    src = s.get(Candidate, (sel.params or {}).get("sheet_candidate_id") or -1)
    return sel, _sheet_stale(s, char, VIEW_SHEET[view], src)


def crop_method(s: Session, char: Character, sheet: str) -> str | None:
    """选定的设定图是怎么切的（segment / equal）；还没切过返回 None。"""
    sel = selected_candidate(s, "character", char.id, sheet_slot_kind(sheet))
    if sel is None:
        return None
    first = selected_candidate(s, "character", char.id, view_slot_kind(SHEETS[sheet].views[0]))
    if first is None or (first.params or {}).get("sheet_candidate_id") != sel.id:
        return None
    return first.params.get("method")


def crop_cells(s: Session, media, char: Character, sheet: str, method: str = "auto") -> str:
    """把选定的设定图切成单个视角存进各格并选定，返回实际用的切法。
    同一张设定图切出同样的框时复用已有的格子，不重复存文件。"""
    spec = SHEETS[sheet]
    src = selected_candidate(s, "character", char.id, sheet_slot_kind(sheet))
    if src is None:
        raise LookupError(f"角色 {char.name} 还没有{spec.label}设定图")
    with Image.open(media.abs(src.file_path)) as im:
        im.load()
        crops, boxes, used = crop_sheet(im.convert("RGB"), len(spec.views), method)  # type: ignore[arg-type]
    slug = char.project.slug
    for view, crop, box in zip(spec.views, crops, boxes):
        slot = get_or_create_slot(s, "character", char.id, view_slot_kind(view))
        box_l = [int(v) for v in box]
        cand = next((c for c in slot.candidates
                     if (c.params or {}).get("sheet_candidate_id") == src.id
                     and (c.params or {}).get("box") == box_l), None)
        if cand is None:
            dst = media.new_media_path(slug, "turnaround", ".png")
            buf = io.BytesIO()
            crop.save(buf, "PNG")
            dst.write_bytes(buf.getvalue())
            cand = add_candidate(s, slot, file_path=media.rel(dst), engine="crop",
                                 params={"view": view, "sheet": sheet,
                                         "sheet_candidate_id": src.id, "box": box_l,
                                         "method": used},
                                 input_hash=cell_hash(src.input_hash, view), job_id=src.job_id,
                                 auto_select=False)
        slot.selected_candidate_id = cand.id
    s.flush()
    return used


def select_sheet(s: Session, media, char: Character, candidate_id: int) -> str:
    """换选定的设定图并立即重新切图。返回设定图种类。"""
    c = s.get(Candidate, candidate_id)
    slot = s.get(Slot, c.slot_id) if c else None
    sheet = next((k for k in SHEET_KEYS if slot is not None and slot.owner_type == "character"
                  and slot.owner_id == char.id and slot.kind == sheet_slot_kind(k)), None)
    if sheet is None:
        raise LookupError(f"候选 {candidate_id} 不是角色 {char.name} 的三视图设定图")
    slot.selected_candidate_id = c.id
    s.flush()
    crop_cells(s, media, char, sheet)
    return sheet


# ---------------------------------------------------------------- 镜头 → 视角
# 想要的那格还没有（或已过期）时依次退到这些格；全都没有就只带定妆照
FALLBACKS: dict[str, tuple[str, ...]] = {
    "face_front": ("face_front", "face_three_quarter", "three_quarter", "front_full"),
    "face_three_quarter": ("face_three_quarter", "face_front", "three_quarter"),
    "face_profile": ("face_profile", "profile", "face_three_quarter"),
    "three_quarter": ("three_quarter", "front_full", "face_three_quarter"),
    "front_full": ("front_full", "three_quarter"),
    "profile": ("profile", "face_profile", "three_quarter"),
    "back": ("back",),
}

_BACK_WORDS = ("背影", "背对", "背面", "back view", "rear view")
_PROFILE_WORDS = ("侧面", "侧脸", "侧身", "profile", "side view")
# 景别 → 视角（按顺序匹配，"大特写"先于"特写"，"中近景"先于"中景"）。仰拍 / 俯拍是机位，
# 设定图里没有，按景别挑最近的正常视角。
_SIZE_WORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("face_front", ("大特写", "特写", "extreme close", "close-up", "close up", "closeup")),
    ("face_three_quarter", ("近景", "medium close", "medium-close", "bust shot")),
    ("three_quarter", ("中景", "medium shot", "mid shot", "medium")),
    ("front_full", ("全景", "远景", "大远景", "wide", "full shot", "long shot", "establishing")),
)
_CLOSE = {"face_front", "face_three_quarter"}
DEFAULT_VIEW = "three_quarter"

# 英文 "from behind" 只在说拍的是人的背后时才算（"sun from behind the camera" 不算）
_FROM_BEHIND = re.compile(r"\b(?:shot|seen|viewed|filmed|photographed|framed|him|her|them)\s+from\s+behind\b"
                          r"|\bfrom\s+behind\s+(?:him|her|them)\b|^from\s+behind\b|[.,;]\s*from\s+behind\b")


def _size_view(shot) -> str:
    size = (shot.shot_size or "").lower()
    for view, keys in _SIZE_WORDS:
        if any(k in size for k in keys):
            return view
    return DEFAULT_VIEW


def view_for_shot(shot) -> str:
    """镜头最匹配的视角：背影 → 背面；侧面 → 侧面（特写 / 近景时用侧脸）；其余按景别。"""
    text = " ".join([shot.shot_size or "", shot.camera_move or "", shot.action or "",
                     shot.image_prompt or ""]).lower()
    if _FROM_BEHIND.search(text) or any(k in text for k in _BACK_WORDS):
        return "back"
    by_size = _size_view(shot)
    if any(k in text for k in _PROFILE_WORDS):
        return "face_profile" if by_size in _CLOSE else "profile"
    return by_size


def pick_cell(s: Session, char: Character, view: str) -> tuple[str, Candidate] | None:
    """按退路依次找第一个已切好且没过期的格。"""
    for v in FALLBACKS.get(view, (view,)):
        sel, is_stale = cell_state(s, char, v)
        if sel is not None and not is_stale:
            return v, sel
    return None
