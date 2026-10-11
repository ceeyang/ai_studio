"""各 LLM 任务的输出结构。校验失败的错误信息会原样喂回模型让它改。"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

TRANSITIONS = ("hard_cut", "dissolve_0.5", "white_flash_0.2", "black_dip_0.3", "fade_out_1.0")
Transition = Literal["hard_cut", "dissolve_0.5", "white_flash_0.2", "black_dip_0.3", "fade_out_1.0"]


# ---------------------------------------------------------------- 剧本结构
class CharacterOut(BaseModel):
    name: str = Field(min_length=1)
    description: str = ""


class LocationOut(BaseModel):
    name: str = Field(min_length=1)
    time_of_day: str = ""
    atmosphere: str = ""


class SceneBlockOut(BaseModel):
    location: str
    text: str = Field(min_length=1)


class ScriptStructure(BaseModel):
    title: str = ""
    characters: list[CharacterOut] = Field(default_factory=list)
    locations: list[LocationOut] = Field(min_length=1)
    scenes: list[SceneBlockOut] = Field(min_length=1)

    @model_validator(mode="after")
    def _scene_locations_exist(self):
        names = {loc.name for loc in self.locations}
        for sc in self.scenes:
            if sc.location not in names:
                raise ValueError(f"场景 {sc.location!r} 不在 locations 列表里，可用：{sorted(names)}")
        return self


# ---------------------------------------------------------------- 视觉设定
class StyleOut(BaseModel):
    anchor_text: str = Field(min_length=40)
    palette: str = ""
    lighting: str = ""
    texture: str = ""
    character_rules: str = ""
    mood_keywords: list[str] = Field(default_factory=list)
    negative: str = ""


class CharacterVisual(BaseModel):
    name: str
    visual_prompt: str = Field(min_length=20)
    core_features: str = ""


class LocationVisual(BaseModel):
    name: str
    visual_prompt: str = Field(min_length=20)


class ScriptVisual(BaseModel):
    style: StyleOut
    characters: list[CharacterVisual] = Field(default_factory=list)
    locations: list[LocationVisual] = Field(default_factory=list)


# ---------------------------------------------------------------- 分镜
class DialogueOut(BaseModel):
    speaker: str
    zh: str = Field(min_length=1, max_length=16)
    start: float = Field(ge=0)
    end: float

    @model_validator(mode="after")
    def _order(self):
        if self.end <= self.start:
            raise ValueError(f"台词「{self.zh}」的 end 必须大于 start")
        return self


class ShotOut(BaseModel):
    shot_size: str = ""
    camera_move: str = ""
    action: str
    cast: list[str] = Field(default_factory=list)
    dialogue: list[DialogueOut] = Field(default_factory=list)
    image_prompt: str
    motion_prompt: str
    audio_prompt: str = ""
    # 本镜出现的关键道具的英文说法（和 image_prompt 里一字不差），后面的段落沿用同一个说法
    props: list[str] = Field(default_factory=list, max_length=8)
    duration: float = Field(default=5.0, ge=2, le=10)
    transition_out: Transition = "hard_cut"
    # 分镜建议这一镜接上一镜（只是建议，存进 Shot.suggest_continue，用户在工作台一键打开）
    continue_from_prev: bool = False

    @field_validator("props", mode="before")
    @classmethod
    def _props_clean(cls, v):
        if v is None:
            return []
        if not isinstance(v, list):
            raise ValueError("props 要写成英文字符串数组")
        return [str(x).strip() for x in v if str(x).strip()][:8]

    @field_validator("image_prompt")
    @classmethod
    def _image_prompt_rules(cls, v: str) -> str:
        # 外貌占位符 {{char:3}} 出图时才换成外貌，不算字数
        words = len(re.sub(r"\{\{\s*char\s*:\s*\d+\s*\}\}", " ", v).split())
        if words < 40 or words > 170:
            raise ValueError(f"image_prompt 你写的部分要 60–120 个英文单词（占位符不算），现在是 {words} 个")
        if "same as" in v.lower():
            raise ValueError("image_prompt 里不能写 same as before / same as previous：每个镜头都要独立写全"
                             "（角色第一次出现带上按景别取舍的外貌，之后用 the stocky man 这样的简短指代；"
                             "场景写看得见的具体物件）")
        return v

    @model_validator(mode="after")
    def _dialogue_within_duration(self):
        for d in self.dialogue:
            if d.end > self.duration:
                raise ValueError(f"台词「{d.zh}」的 end={d.end} 超过了镜头 duration={self.duration}")
        return self


class StoryboardOut(BaseModel):
    shots: list[ShotOut] = Field(min_length=1)


# ---------------------------------------------------------------- 字幕翻译
class TranslatedLine(BaseModel):
    id: str
    km: str = Field(min_length=1)


class TranslationOut(BaseModel):
    lines: list[TranslatedLine] = Field(min_length=1)


# ---------------------------------------------------------------- 按高棉语时长改写中文
class FittedLine(BaseModel):
    id: str
    zh: str = Field(min_length=1, max_length=60)


class FitOut(BaseModel):
    lines: list[FittedLine] = Field(min_length=1)


# ---------------------------------------------------------------- 角色造型
def _look_words(v: str) -> str:
    n = len(v.split())
    if n < 50 or n > 140:
        raise ValueError(f"visual_prompt 要写 60–120 个英文单词，现在是 {n} 个")
    return v


class DesignOption(BaseModel):
    label: str = Field(min_length=1, max_length=40)
    visual_prompt: str
    core_features: str = ""

    @field_validator("visual_prompt")
    @classmethod
    def _words(cls, v: str) -> str:
        return _look_words(v)


class DesignOptions(BaseModel):
    options: list[DesignOption] = Field(min_length=1)


class RecommendedCharacter(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = ""
    visual_prompt: str
    core_features: str = ""

    @field_validator("visual_prompt")
    @classmethod
    def _words(cls, v: str) -> str:
        return _look_words(v)


class CharacterRecommendations(BaseModel):
    characters: list[RecommendedCharacter] = Field(default_factory=list)


class PhotoLook(BaseModel):
    """从参考照片写的外观：英文外观提示词 + 中文核心特征。"""
    visual_prompt: str
    core_features: str = ""

    @field_validator("visual_prompt")
    @classmethod
    def _words(cls, v: str) -> str:
        return " ".join(_look_words(v).split())


# ---------------------------------------------------------------- 三视图
class TurnaroundLook(BaseModel):
    """出三视图设定图用的全身描述：一段英文，从头到脚。"""
    description: str

    @field_validator("description")
    @classmethod
    def _words(cls, v: str) -> str:
        n = len(v.split())
        if n < 30 or n > 160:
            raise ValueError(f"description 要写 50–130 个英文单词，现在是 {n} 个")
        return " ".join(v.split())


# ---------------------------------------------------------------- 剧本体检
ReviewDimension = Literal["hook", "conflict", "ending", "pacing"]
REVIEW_DIMENSIONS: dict[str, str] = {"hook": "开场钩子（前 15 秒）", "conflict": "冲突与爽点密度",
                                     "ending": "结尾卡点", "pacing": "节奏"}
REVIEW_MAX_SUGGESTIONS = 5


class ReviewScore(BaseModel):
    score: int = Field(ge=0, le=10)
    reason: str = Field(min_length=1)


class ReviewSuggestion(BaseModel):
    dimension: ReviewDimension
    ref: str = Field(min_length=1, max_length=20)   # 镜头编号 S03，或「段落 2」
    problem: str = ""
    rewrite: str = Field(min_length=1)


class ScriptReviewOut(BaseModel):
    hook: ReviewScore
    conflict: ReviewScore
    ending: ReviewScore
    pacing: ReviewScore
    summary: str = ""
    suggestions: list[ReviewSuggestion] = Field(default_factory=list)

    @field_validator("suggestions", mode="before")
    @classmethod
    def _at_most(cls, v):
        # 多给的建议不为这个让模型重写：只留前几条（模板要求按影响从大到小排）
        return v[:REVIEW_MAX_SUGGESTIONS] if isinstance(v, list) else v
