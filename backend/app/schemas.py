"""HTTP API 的输入输出模型。"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.llm.schemas import Transition

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


class ShotFields(BaseModel):
    shot_size: str | None = None
    camera_move: str | None = None
    action: str | None = None
    dialogue: list[dict] | None = None
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


class RunOut(BaseModel):
    job_ids: list[int]


class CandidatePatch(BaseModel):
    note: str | None = None
    rating: int | None = Field(None, ge=0, le=5)


class RetryIn(BaseModel):
    payload: dict | None = None


class InputIn(BaseModel):
    text: str = Field(min_length=1)
