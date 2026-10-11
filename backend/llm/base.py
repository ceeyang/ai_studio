"""LLM provider 的公共类型。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class LLMResult:
    text: str
    model: str | None = None
    usage: dict | None = None
    seconds: float = 0.0


class LLMError(RuntimeError):
    pass


class VisionRejected(LLMError):
    """带图片的请求被接口拒了（多半是模型不支持看图）。网关会换成「去设置页换模型」的提示。"""


class LLMProvider(Protocol):
    """能看图的 provider 把 supports_images 设为 True，complete 接受 images（本机图片路径，
    附在第一条用户消息上）。不支持的 provider 不会收到 images（网关先报错）。"""
    id: str

    def complete(self, system: str, messages: list[dict], *, json_mode: bool = True) -> LLMResult: ...


IMAGE_MEDIA_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                     ".webp": "image/webp", ".gif": "image/gif"}


def image_media_type(path) -> str:
    from pathlib import Path

    ext = Path(path).suffix.lower()
    if ext not in IMAGE_MEDIA_TYPES:
        raise LLMError(f"不支持的图片格式：{Path(path).name}（只支持 png / jpg / webp）")
    return IMAGE_MEDIA_TYPES[ext]


def image_b64(path) -> tuple[str, str]:
    """(媒体类型, base64 数据)。"""
    import base64
    from pathlib import Path

    mt = image_media_type(path)
    try:
        data = Path(path).read_bytes()
    except OSError as e:
        raise LLMError(f"读不到图片 {path}：{e}") from None
    return mt, base64.b64encode(data).decode("ascii")
