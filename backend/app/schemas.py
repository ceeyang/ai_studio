"""HTTP API 的输入输出模型。"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.llm.schemas import Transition, TurnaroundView
from backend.post.options import ExportOptions

Aspect = Literal["9:16", "16:9"]
Stage = Literal["script", "storyboard", "keyframes", "videos", "export", "all"]


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ProjectIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    slug: str | None = None
    aspect: Aspect = "9:16"
    image_engine: str = "zimage"
    video_engine: str = "h3_video"
    seed: int = 12345
    negative: str = ""


class ProjectPatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=200)
    aspect: Aspect | None = None
    image_engine: str | None = None
    video_engine: str | None = None
    seed: int | None = None
    negative: str | None = None


class ProjectOut(ORM):
    id: int
    slug: str
    title: str
    aspect: str
    image_engine: str
    video_engine: str
    seed: int
    negative: str
    style_bible_id: int | None
    created_at: datetime


class StyleIO(ORM):
    anchor_text: str = ""
    palette: str = ""
    lighting: str = ""
    texture: str = ""
    character_rules: str = ""
    mood_keywords: list[str] = Field(default_factory=list)
    negative: str = ""


class CharacterOut(ORM):
    id: int
    project_id: int
    name: str
    description: str
    visual_prompt: str
    core_features: str
    voice: str | None
    portrait_candidate_id: int | None = None
    portrait_url: str | None = None
    portraits: list[CandidateOut] = Field(default_factory=list)   # 全部定妆照候选（上传的 + AI 抽的）
    design_options: list[dict] = Field(default_factory=list)      # AI 推荐造型
    active_jobs: list[JobBrief] = Field(default_factory=list)     # 进行中的抽卡 / 推荐造型 / 三视图任务
    turnaround: list[TurnaroundCellOut] = Field(default_factory=list)  # 三视图 9 格，固定顺序


class JobBrief(BaseModel):
    id: int
    type: str
    status: str
    view: str | None = None              # image.turnaround 任务出的是哪一格


class TurnaroundCellOut(BaseModel):
    view: str
    label: str
    description: str                     # 视角描述（英文），空表示还没写
    selected_candidate_id: int | None
    url: str | None
    stale: bool                          # 选定的这张是按旧定妆照 / 旧描述出的
    candidates: list[CandidateOut]


class TurnaroundPlanPatch(BaseModel):
    views: dict[TurnaroundView, str]


class TurnaroundGenerateIn(BaseModel):
    views: list[TurnaroundView] | None = None   # 不给就 9 格全出


class PortraitDrawIn(BaseModel):
    count: int = Field(1, ge=1, le=4)
    visual_prompt: str | None = None     # 不给就用角色当前的外貌提示词


class SuggestIn(BaseModel):
    count: int = Field(3, ge=1, le=6)
    episode_id: int | None = None        # 读哪一集的剧本；不给读全部剧集


class RecommendIn(BaseModel):
    count: int = Field(3, ge=1, le=6)


class CharacterIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = ""
    visual_prompt: str = ""
    core_features: str = ""


class PortraitPathIn(BaseModel):
    path: str = Field(min_length=1)


class CharacterPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    visual_prompt: str | None = None
    core_features: str | None = None
    voice: str | None = None


class LocationOut(ORM):
    id: int
    project_id: int
    name: str
    time_of_day: str
    atmosphere: str
    visual_prompt: str


class LocationPatch(BaseModel):
    name: str | None = None
    time_of_day: str | None = None
    atmosphere: str | None = None
    visual_prompt: str | None = None


class EpisodeIn(BaseModel):
    title: str = ""
    source_text: str = ""
    target_seconds: float = Field(60.0, gt=0, le=3600)
    number: int | None = Field(None, ge=1)


class EpisodePatch(BaseModel):
    title: str | None = None
    source_text: str | None = None
    target_seconds: float | None = Field(None, gt=0, le=3600)


class EpisodeOut(ORM):
    id: int
    project_id: int
    number: int
    title: str
    source_text: str
    target_seconds: float
    status: str


class EpisodeStats(BaseModel):
    id: int
    number: int
    title: str
    status: str
    shot_count: int
    export_count: int
    cover_url: str | None


class ProjectOverview(BaseModel):
    episode_count: int
    cover_url: str | None
    episodes: list[EpisodeStats]


class SceneBlockOut(ORM):
    id: int
    order: int
    location_id: int | None
    text: str


class SceneBlockPatch(BaseModel):
    text: str | None = None
    location_id: int | None = None


class CandidateOut(ORM):
    id: int
    slot_id: int
    file_path: str
    url: str = ""
    engine: str
    params: dict
    input_hash: str
    job_id: int | None
    note: str
    rating: int | None
    created_at: datetime


class ExportOut(ORM):
    id: int
    episode_id: int
    variant: str
    file_path: str
    url: str = ""
    settings: dict
    created_at: datetime


class SlotOut(BaseModel):
    id: int
    kind: str
    selected_candidate_id: int | None
    stale: bool = False
    candidates: list[CandidateOut] = Field(default_factory=list)


class DialogueIn(BaseModel):
    """一句台词。km 是高棉语字幕/配音稿；km_review：ai_draft = AI 译稿待母语校对，checked = 已校对。"""
    model_config = ConfigDict(extra="allow")
    speaker: str = ""
    zh: str = ""
    start: float = Field(0.0, ge=0)
    end: float = Field(0.0, ge=0)
    km: str | None = None
    km_review: Literal["ai_draft", "checked"] | None = None

    @model_validator(mode="after")
    def _order(self):
        if self.end < self.start:
            raise ValueError(f"台词「{self.zh}」的 end 不能早于 start")
        return self


class ShotFields(BaseModel):
    shot_size: str | None = None
    camera_move: str | None = None
    action: str | None = None
    dialogue: list[DialogueIn] | None = None
    cast: list[dict] | None = None
    props: list | None = None
    image_prompt: str | None = None
    motion_prompt: str | None = None
    audio_prompt: str | None = None
    image_engine: str | None = None
    video_engine: str | None = None
    duration: float | None = Field(None, gt=0, le=30)
    use_end_frame: bool | None = None
    continue_from_prev: bool | None = None
    transition_out: Transition | None = None


class ShotIn(ShotFields):
    after_shot_id: int | None = None


class ShotOut(ORM):
    id: int
    episode_id: int
    scene_block_id: int | None
    order: int
    code: str
    shot_size: str
    camera_move: str
    action: str
    dialogue: list
    cast: list
    props: list
    image_prompt: str
    motion_prompt: str
    audio_prompt: str
    image_engine: str | None
    video_engine: str | None
    duration: float
    use_end_frame: bool
    continue_from_prev: bool
    transition_out: str


class ShotDetail(ShotOut):
    slots: list[SlotOut] = Field(default_factory=list)


class EpisodeDetail(EpisodeOut):
    scene_blocks: list[SceneBlockOut] = Field(default_factory=list)
    shots: list[ShotDetail] = Field(default_factory=list)


class ReorderIn(BaseModel):
    shot_ids: list[int]


class RunIn(BaseModel):
    stage: Stage
    shots: list[str] | None = None
    candidates: int = Field(1, ge=1, le=4)
    force: bool = False
    priority: int = 0
    export: ExportOptions | None = None   # 只对 export 阶段有效：字幕 / 配音 / 片尾卡 / 裁尾


class TranslateIn(BaseModel):
    force: bool = False                                   # 已有译文的也重翻
    glossary: dict[str, str] = Field(default_factory=dict)  # 固定译名，如店名


class EndCardLine(BaseModel):
    text: str = Field("", max_length=200)
    font: str | None = Field(None, max_length=100)        # 留空用 subtitles.latin_font
    size: int = Field(40, ge=8, le=240)                   # 1080×1920 画面上的像素字号
    bold: bool = False


class EndCardIO(BaseModel):
    seconds: float = Field(3.0, ge=1, le=10)
    background: str = Field("#0b1a33", pattern=r"^#[0-9a-fA-F]{6}$")
    lines: list[EndCardLine] = Field(default_factory=list, max_length=16)


class RunOut(BaseModel):
    job_ids: list[int]


class CandidatePatch(BaseModel):
    note: str | None = None
    rating: int | None = Field(None, ge=0, le=5)


class RetryIn(BaseModel):
    payload: dict | None = None


class InputIn(BaseModel):
    text: str = Field(min_length=1)


TurnaroundCellOut.model_rebuild()
CharacterOut.model_rebuild()  # portraits / turnaround 引用了后面定义的 CandidateOut
