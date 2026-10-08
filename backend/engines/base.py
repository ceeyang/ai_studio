"""引擎接口。build() 只负责把请求拼成调用（ComfyUI 工作流），执行交给 runner。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

UPLOAD_PREFIX = "@upload:"


def upload_ref(path: Path) -> str:
    """在工作流里标记一张要上传的本地图片，runner 提交前会换成上传后的名字。"""
    return UPLOAD_PREFIX + str(Path(path).resolve())


@dataclass(frozen=True)
class ImageCaps:
    max_refs: int
    sizes: dict[str, tuple[int, int]]
    supports_negative: bool


@dataclass(frozen=True)
class VideoCaps:
    max_refs: int
    supports_end_frame: bool
    has_audio: bool
    fps: int
    sizes: dict[str, tuple[int, int]]
    default_seconds: float
    max_seconds: float


def size_for(sizes: dict[str, tuple[int, int]], aspect: str) -> tuple[int, int]:
    if aspect not in sizes:
        raise ValueError(f"不支持画幅 {aspect}，可选：{', '.join(sizes)}")
    return sizes[aspect]


@dataclass
class ImageRequest:
    prompt: str
    aspect: str = "9:16"
    seed: int = 0
    negative: str = ""
    refs: list[Path] = field(default_factory=list)
    prefix: str = "studio/img"


@dataclass
class VideoRequest:
    prompt: str
    first_frame: Path
    aspect: str = "9:16"
    seed: int = 0
    seconds: float = 5.0
    negative: str = ""
    end_frame: Path | None = None
    refs: list[Path] = field(default_factory=list)
    prefix: str = "studio/vid"


@dataclass
class EngineCall:
    engine: str
    workflow: dict
    output_kind: Literal["image", "video"]
    model_family: str
    meta: dict = field(default_factory=dict)
    dropped_refs: list[str] = field(default_factory=list)


class ImageEngine(Protocol):
    name: str
    family: str
    caps: ImageCaps

    def build(self, req: ImageRequest) -> EngineCall: ...


class VideoEngine(Protocol):
    name: str
    family: str
    caps: VideoCaps

    def frames_for(self, seconds: float) -> int: ...

    def build(self, req: VideoRequest) -> EngineCall: ...
