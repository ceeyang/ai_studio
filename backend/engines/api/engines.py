"""官方 API 的出图 / 视频引擎：OpenAI 图像（gpt-image-1）、Gemini 图像（gemini-2.5-flash-image）、
Google Veo（Gemini API）、OpenAI Sora。

build() 只把请求拼成「API 调用请求」（EngineCall.runner="api"，workflow 里是服务、操作、模型、提示词、
画幅、参考图 / 首帧的本机路径），执行交给 ApiRunner（engines/api/runner.py）。workflow 会原样写进任务目录的
workflow.api.json，所以里面不能有 Key：Key 在 runner 发请求时现读、只放请求头。

这几家都没有种子参数（结果每次不同）；OpenAI 图像、Sora 没有负面提示词（丢掉，meta 里记 negative_ignored，
和网页出图一样）；Veo 有 negativePrompt。不占本机显卡：每个引擎族一条串行通道（services.py 的 lanes）。

params（config.yaml engines.<name>.params，可省略）：
  model: 模型名（设置页「API 服务」里填了的优先）
  timeout: 等结果的最长秒数
  fit_aspect: 出来的图宽高比不对时居中裁成要求的画幅（图像引擎，默认 true）
  quality: OpenAI 图像的 quality（low / medium / high / auto，默认不传 = auto）
"""
from __future__ import annotations

from collections.abc import Callable

from backend.domain.api_keys import Creds, env_creds, no_key_hint
from backend.engines.base import (EngineCall, ImageCaps, ImageRequest, VideoCaps, VideoRequest,
                                  size_for)

RUNTIME = "api"
CredsFn = Callable[[str], Creds]

ASPECT_TEXT = {
    "9:16": "vertical 9:16 portrait format (tall image)",
    "16:9": "horizontal 16:9 landscape format (wide image)",
    "1:1": "square 1:1 format",
    "3:4": "vertical 3:4 portrait format",
    "4:3": "horizontal 4:3 landscape format",
    "21:9": "ultra-wide 21:9 panoramic format",
}

# OpenAI 图像只有三种尺寸：竖 1024x1536（2:3）、横 1536x1024（3:2）、方 1024x1024。
# 先按最接近的出，再居中裁成要求的画幅（和网页出图一样）。caps.sizes 写的是裁完的大小。
OPENAI_IMAGE_SIZE = {"9:16": "1024x1536", "3:4": "1024x1536", "16:9": "1536x1024", "4:3": "1536x1024",
                     "21:9": "1536x1024", "1:1": "1024x1024"}
OPENAI_IMAGE_OUT = {"9:16": (864, 1536), "3:4": (1024, 1365), "16:9": (1536, 864), "4:3": (1365, 1024),
                    "21:9": (1536, 658), "1:1": (1024, 1024)}
# Gemini 图像直接认 aspectRatio（1:1、2:3、3:2、3:4、4:3、4:5、5:4、9:16、16:9、21:9），尺寸是官方文档里的
GEMINI_IMAGE_OUT = {"9:16": (768, 1344), "16:9": (1344, 768), "1:1": (1024, 1024), "3:4": (864, 1184),
                    "4:3": (1184, 864), "21:9": (1536, 672)}
# 视频：Veo 和 Sora 都只有竖 / 横两种（720p）
VIDEO_SIZES = {"9:16": (720, 1280), "16:9": (1280, 720)}
SORA_SECONDS = (4, 8, 12)          # sora-2 的 seconds 只能是 "4" / "8" / "12"
VEO31_SECONDS = (4, 6, 8)          # veo-3.1 的 durationSeconds；veo-3.0 固定 8 秒，不传


def api_prompt(prompt: str, aspect: str, n_refs: int) -> str:
    """发给出图 API 的提示词：画幅写进去（OpenAI 只有 2:3 / 3:2 / 1:1，按画幅构图后再裁），参考图说明。"""
    parts = [" ".join((prompt or "").split())]
    if aspect in ASPECT_TEXT:
        parts.append(f"Image format: {ASPECT_TEXT[aspect]}; fill the whole frame edge to edge, "
                     "no borders, no letterboxing, no text or watermark.")
    if n_refs:
        pics = "image is a reference" if n_refs == 1 else f"{n_refs} images are references"
        parts.append(f"The attached {pics}: keep the same person exactly (face, hairstyle, skin tone, "
                     "build, outfit details), but follow the description above for pose, framing, "
                     "background and lighting.")
    return " ".join(parts)


class _ApiEngine:
    name = ""
    family = ""
    label = ""
    service = ""
    model_kind = "image"
    runtime = RUNTIME
    default_timeout = 300.0

    def __init__(self, overrides: dict | None = None, *, creds: CredsFn | None = None):
        self.overrides = dict(overrides or {})
        self._creds = creds or env_creds

    def creds(self) -> Creds:
        return self._creds(self.service)

    def model(self) -> str:
        """设置页「API 服务」里填的 → config.yaml engines.<name>.params.model → 默认常量。"""
        return self.creds().model(self.model_kind, self.overrides.get("model"))

    def preflight(self) -> str | None:
        """提交前检查：没有 Key 直接拒绝（不发请求）。"""
        if not self.creds().configured:
            return f"{self.label}不能用：{no_key_hint(self.service)}"
        return None

    def timeout(self) -> float:
        return float(self.overrides.get("timeout") or self.default_timeout)


class _ApiImageEngine(_ApiEngine):
    model_kind = "image"

    def _request(self, req: ImageRequest, keep) -> dict:  # pragma: no cover - 子类实现
        raise NotImplementedError

    def build(self, req: ImageRequest) -> EngineCall:
        w, h = size_for(self.caps.sizes, req.aspect)
        keep, dropped = req.refs[: self.caps.max_refs], req.refs[self.caps.max_refs:]
        text = api_prompt(req.prompt, req.aspect, len(keep))
        model = self.model()
        request = {"runner": RUNTIME, "service": self.service, "op": "image", "model": model,
                   "prompt": text, "aspect": req.aspect, "refs": [str(p) for p in keep],
                   "timeout": self.timeout(), "fit_aspect": bool(self.overrides.get("fit_aspect", True)),
                   **self._request(req, keep)}
        return EngineCall(self.name, request, "image", self.family,
                          meta={"prompt": req.prompt, "api_prompt": text, "service": self.service,
                                "model": model, "size": [w, h], "seed": None, "nondeterministic": True,
                                "refs": [str(p) for p in keep],
                                **({"negative_ignored": req.negative} if req.negative else {})},
                          dropped_refs=[str(p) for p in dropped], runner="api")


class OpenAIImageEngine(_ApiImageEngine):
    """OpenAI 图像 API：没有参考图走 /images/generations，有参考图走 /images/edits（multipart，多张 image[]）。"""
    name = "openai_image"
    family = "openai_api_image"
    label = "OpenAI 图像（API）"
    service = "openai"
    default_timeout = 300.0
    caps = ImageCaps(max_refs=8, supports_negative=False, sizes=OPENAI_IMAGE_OUT)

    def _request(self, req, keep) -> dict:
        out = {"size": OPENAI_IMAGE_SIZE[req.aspect]}
        if self.overrides.get("quality"):
            out["quality"] = str(self.overrides["quality"])
        return out


class GeminiApiImageEngine(_ApiImageEngine):
    """Gemini 图像（generateContent，responseModalities=IMAGE，imageConfig.aspectRatio）。参考图作 inlineData。"""
    name = "gemini_api_image"
    family = "gemini_api_image"
    label = "Gemini 图像（API）"
    service = "gemini"
    default_timeout = 300.0
    caps = ImageCaps(max_refs=3, supports_negative=False, sizes=GEMINI_IMAGE_OUT)

    def _request(self, req, keep) -> dict:
        return {"aspect_ratio": req.aspect}


class _ApiVideoEngine(_ApiEngine):
    model_kind = "video"
    default_timeout = 1200.0

    def frames_for(self, seconds: float) -> int:
        return max(1, round(seconds * self.caps.fps))

    def _request(self, req: VideoRequest, seconds: int | None) -> dict:  # pragma: no cover
        raise NotImplementedError

    def _seconds(self, req: VideoRequest, model: str) -> int | None:  # pragma: no cover
        raise NotImplementedError

    def build(self, req: VideoRequest) -> EngineCall:
        w, h = size_for(self.caps.sizes, req.aspect)
        model = self.model()
        seconds = self._seconds(req, model)
        out_seconds = float(seconds or self.caps.default_seconds)
        request = {"runner": RUNTIME, "service": self.service, "op": "video", "model": model,
                   "prompt": " ".join((req.prompt or "").split()), "aspect": req.aspect,
                   "size": f"{w}x{h}", "first_frame": str(req.first_frame), "seconds": seconds,
                   "timeout": self.timeout(), **self._request(req, seconds)}
        dropped = [str(p) for p in req.refs]   # 这两家的图生视频只吃首帧，参考图用不上
        meta = {"prompt": req.prompt, "service": self.service, "model": model, "seed": None,
                "nondeterministic": True, "size": [w, h], "seconds": out_seconds,
                "frames": self.frames_for(out_seconds), "fps": self.caps.fps,
                "first_frame": str(req.first_frame), "refs": []}
        if req.negative and "negative" not in request:
            meta["negative_ignored"] = req.negative
        return EngineCall(self.name, request, "video", self.family, meta=meta, dropped_refs=dropped,
                          runner="api")


class VeoEngine(_ApiVideoEngine):
    """Google Veo（Gemini API predictLongRunning）：首帧图生视频，有声。veo-3.0 固定 8 秒；veo-3.1 可选 4/6/8 秒。"""
    name = "veo_video"
    family = "veo_api"
    label = "Google Veo（API）"
    service = "gemini"
    caps = VideoCaps(max_refs=0, supports_end_frame=False, has_audio=True, fps=24, sizes=VIDEO_SIZES,
                     default_seconds=8.0, max_seconds=8.0, allowed_seconds=VEO31_SECONDS)

    def _seconds(self, req, model) -> int | None:
        if "3.1" not in model:
            return None
        want = max(req.seconds, req.min_seconds)
        return next((s for s in VEO31_SECONDS if s >= want - 1e-6), VEO31_SECONDS[-1])

    def _request(self, req, seconds) -> dict:
        return {"negative": req.negative or None}


class SoraEngine(_ApiVideoEngine):
    """OpenAI Sora（POST /videos，input_reference 当首帧）：有声，4 / 8 / 12 秒。"""
    name = "sora_video"
    family = "sora_api"
    label = "OpenAI Sora（API）"
    service = "openai"
    caps = VideoCaps(max_refs=0, supports_end_frame=False, has_audio=True, fps=30, sizes=VIDEO_SIZES,
                     default_seconds=4.0, max_seconds=12.0, allowed_seconds=SORA_SECONDS)

    def _seconds(self, req, model) -> int:
        want = max(req.seconds, req.min_seconds)
        return next((s for s in SORA_SECONDS if s >= want - 1e-6), SORA_SECONDS[-1])

    def _request(self, req, seconds) -> dict:
        return {}


API_ENGINES = (OpenAIImageEngine, GeminiApiImageEngine, VeoEngine, SoraEngine)
# 队列给每个 API 引擎族开一条串行通道（不走 GPU worker）
API_FAMILIES: tuple[str, ...] = tuple(e.family for e in API_ENGINES)
