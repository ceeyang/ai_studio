"""各 LLM 任务的输出结构。校验失败的错误信息会原样喂回模型让它改。"""
from __future__ import annotations

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
    duration: float = Field(default=5.0, ge=2, le=10)
    transition_out: Transition = "hard_cut"

    @field_validator("image_prompt")
    @classmethod
    def _image_prompt_rules(cls, v: str) -> str:
        words = len(v.split())
        if words < 60 or words > 180:
            raise ValueError(f"image_prompt 要写 80–150 个英文单词，现在是 {words} 个")
        if "same as" in v.lower():
            raise ValueError("image_prompt 里不能写 same as before / same as previous，必须完整复述外貌与场景")
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
