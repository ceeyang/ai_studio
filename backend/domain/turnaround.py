"""角色三视图：9 个固定视角，每格一个槽位 Slot(owner_type="character", kind="turnaround_<view>")。

LLM 先给每格写视角描述（存在 Character.turnaround_plan），用户确认后逐格出图，出图时带选定的
定妆照当参考图锁脸。首帧 / 视频打包参考图时按镜头的景别和机位挑最匹配的一格（见 view_for_shot）。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from backend.domain.characters import portrait_of
from backend.domain.hashing import input_hash
from backend.domain.models import Candidate, Character
from backend.domain.slots import selected_candidate

VIEWS: tuple[tuple[str, str], ...] = (
    ("front_full", "正面全身"),
    ("front_half", "正面半身"),
    ("face_closeup", "脸部特写"),
    ("left_profile", "左侧"),
    ("right_profile", "右侧"),
    ("three_quarter", "3/4 侧"),
    ("back", "背面"),
    ("low_angle", "仰视"),
    ("high_angle", "俯视"),
)
VIEW_KEYS: tuple[str, ...] = tuple(k for k, _ in VIEWS)
VIEW_LABELS: dict[str, str] = dict(VIEWS)

# 每格固定的取景要求（英文，写进出图提示词）；LLM 写的描述补充这一格能看到的服装细节和姿态
VIEW_FRAMES: dict[str, str] = {
    "front_full": "Full-body front view, the whole figure visible from head to shoes, standing upright "
                  "and facing the camera, arms relaxed at the sides, eye-level camera.",
    "front_half": "Front-facing half-body shot from the waist up, facing the camera, eye-level camera.",
    "face_closeup": "Tight close-up of the face and neck, head and shoulders filling the frame, "
                    "facing the camera, eye-level camera.",
    "left_profile": "Full-body left side profile view: the camera sees the person's left side, "
                    "the person faces toward the left edge of the frame, standing upright.",
    "right_profile": "Full-body right side profile view: the camera sees the person's right side, "
                     "the person faces toward the right edge of the frame, standing upright.",
    "three_quarter": "Three-quarter view of the upper body, the body turned about 45 degrees away from "
                     "the camera, face still clearly visible, eye-level camera.",
    "back": "Full-body back view from behind: the person stands facing away from the camera, the back "
            "of the head and the back of the clothes visible, the face not visible.",
    "low_angle": "Low-angle shot: the camera is below waist height looking up at the person, "
                 "who stands upright, full body visible against the backdrop.",
    "high_angle": "High-angle shot: the camera is above head height looking down at the person, "
                  "who stands and looks up toward the camera, full body visible.",
}
TURNAROUND_LEAD = ("Character turnaround reference image of one single person, the same person as in "
                   "the reference image, same face, hairstyle and outfit.")
TURNAROUND_BACKDROP = ("Plain seamless neutral light-grey studio backdrop, soft even studio lighting, "
                       "no props, no other people, no text, no watermark.")

# 想要的那格还没出图（或已过期）时依次退到这些格；全都没有就只带定妆照
FALLBACKS: dict[str, tuple[str, ...]] = {
    "face_closeup": ("face_closeup", "front_half", "three_quarter"),
    "front_half": ("front_half", "three_quarter", "face_closeup"),
    "three_quarter": ("three_quarter", "front_half", "front_full"),
    "front_full": ("front_full", "three_quarter", "front_half"),
    "back": ("back",),
    "left_profile": ("left_profile", "right_profile", "three_quarter"),
    "right_profile": ("right_profile", "left_profile", "three_quarter"),
    "low_angle": ("low_angle", "front_full"),
    "high_angle": ("high_angle", "front_full"),
}

# 镜头文字里的机位关键词，优先于景别（按顺序匹配）
_ANGLE_WORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("back", ("背影", "背对", "背面", "from behind", "back view", "rear view")),
    ("low_angle", ("仰拍", "仰视", "低机位", "low angle", "low-angle", "worm's-eye", "worm's eye")),
    ("high_angle", ("俯拍", "俯视", "高机位", "high angle", "high-angle", "bird's-eye", "bird's eye",
                    "overhead shot")),
    ("left_profile", ("侧面", "侧脸", "侧身", "profile", "side view")),
)
# 景别 → 视角（按顺序匹配，"大特写"先于"特写"，"中近景"先于"中景"）
_SIZE_WORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("face_closeup", ("大特写", "特写", "extreme close", "close-up", "close up", "closeup")),
    ("front_half", ("近景", "medium close", "medium-close", "bust shot")),
    ("three_quarter", ("中景", "medium shot", "mid shot", "medium")),
    ("front_full", ("全景", "远景", "大远景", "wide", "full shot", "long shot", "establishing")),
)
DEFAULT_VIEW = "three_quarter"


def slot_kind(view: str) -> str:
    return f"turnaround_{view}"


def view_for_shot(shot) -> str:
    """镜头最匹配的三视图格：先看镜头文字里的机位词（背影 / 仰拍 / 俯拍 / 侧面），再看景别。"""
    text = " ".join([shot.shot_size or "", shot.camera_move or "", shot.action or "",
                     shot.image_prompt or ""]).lower()
    for view, keys in _ANGLE_WORDS:
        if any(k in text for k in keys):
            return view
    size = (shot.shot_size or "").lower()
    for view, keys in _SIZE_WORDS:
        if any(k in size for k in keys):
            return view
    return DEFAULT_VIEW


def turnaround_prompt(visual_prompt: str, view: str, description: str, anchor: str) -> str:
    from backend.pipeline.prompting import compose_image_prompt  # 包 backend.pipeline 会反过来导入本模块

    body = " ".join(p.strip() for p in (TURNAROUND_LEAD, VIEW_FRAMES[view], description,
                                        visual_prompt, TURNAROUND_BACKDROP) if p and p.strip())
    return compose_image_prompt(body, anchor)


def turnaround_hash(prompt: str, negative: str, engine_name: str, portrait_id: int | None) -> str:
    """不含种子：重出同一格输入不变，换定妆照 / 改描述 / 改外貌提示词 / 换引擎才算过期。"""
    return input_hash("turnaround", prompt, negative, engine_name, portrait_id)


def turnaround_inputs(char: Character, view: str) -> tuple[str, str, str] | None:
    """(prompt, negative, engine)；这一格还没有描述时返回 None。"""
    desc = ((char.turnaround_plan or {}).get(view) or "").strip()
    if not desc:
        return None
    from backend.pipeline.prompting import join_negative

    proj = char.project
    style = proj.style_bible
    prompt = turnaround_prompt(char.visual_prompt or "", view, desc,
                               style.anchor_text if style else "")
    negative = join_negative(proj.negative, style.negative if style else "")
    return prompt, negative, proj.image_engine


def current_hash(s: Session, char: Character, view: str) -> str | None:
    inputs = turnaround_inputs(char, view)
    if inputs is None:
        return None
    prompt, negative, engine = inputs
    p = portrait_of(s, char.id)
    return turnaround_hash(prompt, negative, engine, p.id if p else None)


def cell_state(s: Session, char: Character, view: str) -> tuple[Candidate | None, bool]:
    """(选定的候选, 是否过期)。没出过图不算过期。"""
    sel = selected_candidate(s, "character", char.id, slot_kind(view))
    if sel is None:
        return None, False
    return sel, sel.input_hash != current_hash(s, char, view)


def pick_cell(s: Session, char: Character, view: str) -> tuple[str, Candidate] | None:
    """按退路依次找第一个已出图且没过期的格。"""
    for v in FALLBACKS.get(view, (view,)):
        sel, is_stale = cell_state(s, char, v)
        if sel is not None and not is_stale:
            return v, sel
    return None
