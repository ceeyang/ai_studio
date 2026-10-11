"""引擎接口。build() 只负责把请求拼成调用（ComfyUI 工作流或网页出图请求），执行交给 runner。"""
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
    # 时长怎么控制：param = 引擎参数精确控制；prompt = 只能写进提示词，模型不一定照做；fixed = 不能指定，
    # 总是 default_seconds。allowed_seconds：只认这几档（空 = 连续可调，到 max_seconds 为止）
    duration_control: Literal["param", "prompt", "fixed"] = "param"
    allowed_seconds: tuple[float, ...] = ()


def dedupe_negative(text: str) -> str:
    """负面词按逗号分项去重（不分大小写，保留第一次出现的写法和顺序）。项目负面词和风格负面词常常一样，
    拼起来会写两遍。只在发给引擎的请求里去重，输入哈希仍按原来的拼法算，已有结果不会过期。"""
    seen: set[str] = set()
    out: list[str] = []
    for item in (text or "").split(","):
        x = item.strip()
        if x and x.lower() not in seen:
            seen.add(x.lower())
            out.append(x)
    return ", ".join(out)


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

    def __post_init__(self):
        self.negative = dedupe_negative(self.negative)


@dataclass
class VideoRequest:
    prompt: str
    first_frame: Path
    aspect: str = "9:16"
    seed: int = 0
    seconds: float = 5.0
    negative: str = ""
    end_frame: Path | None = None
    # 至少要出多长（秒）：最后一句台词说完再留一点。帧数网格往下取会比分镜短，台词会被截掉
    min_seconds: float = 0.0
    refs: list[Path] = field(default_factory=list)
    prefix: str = "studio/vid"

    def __post_init__(self):
        self.negative = dedupe_negative(self.negative)


@dataclass
class EngineCall:
    engine: str
    workflow: dict
    output_kind: Literal["image", "video"]
    model_family: str
    meta: dict = field(default_factory=dict)
    dropped_refs: list[str] = field(default_factory=list)
    # 谁来执行：comfyui（workflow 是 API 格式工作流）、browser（网页出图请求，见 engines/browser）、
    # runninghub（云端工作流）或 api（官方 API 请求，见 engines/api；里面没有 Key）
    runner: Literal["comfyui", "browser", "runninghub", "api"] = "comfyui"


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
