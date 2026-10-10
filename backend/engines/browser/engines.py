"""网页图像引擎：gpt_image（chatgpt.com）、gemini_image（gemini.google.com）。

build() 只把请求拼成「网页出图请求」（EngineCall.runner="browser"，workflow 里是网站、提示词、
参考图、画幅），执行交给 BrowserRunner。网页没有尺寸、种子、负面提示词参数：画幅和尺寸写进提示词，
结果每次不同（没有种子），负面提示词丢掉。出图后按画幅居中裁切（params.fit_aspect，默认开）。

params（config.yaml engines.<name>.params）：
  timeout: 等出图的最长秒数（gpt 默认 420，gemini 默认 300）
  fit_aspect: 出来的图宽高比不对时居中裁成要求的画幅（默认 true）
"""
from __future__ import annotations

from backend.engines.base import EngineCall, ImageCaps, ImageRequest, size_for

# 写进提示词的画幅说明。尺寸只是给网站的提示，实际尺寸以出图为准。
ASPECT_TEXT = {
    "9:16": "vertical 9:16 portrait format (tall image, about 1080×1920 pixels)",
    "16:9": "horizontal 16:9 landscape format (wide image, about 1920×1080 pixels)",
    "1:1": "square 1:1 format (about 1024×1024 pixels)",
    "21:9": "ultra-wide 21:9 panoramic format (very wide image, about 2520×1080 pixels)",
}
SIZES = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1024, 1024), "21:9": (2520, 1080)}

RUNTIME = "browser"


def web_prompt(prompt: str, aspect: str, n_refs: int) -> str:
    """一行写完（不能有换行：有的输入框把换行当成发送）。"""
    parts = ["Generate one image now using your image generation tool.",
             "Do not ask any questions and do not answer with text only.",
             f"Image format: {ASPECT_TEXT[aspect]}; fill the whole frame edge to edge, no borders, "
             "no letterboxing, no watermark."]
    if n_refs:
        pics = "image is a reference" if n_refs == 1 else f"{n_refs} images are references"
        parts.append(f"The attached {pics}: keep the same person exactly (face, hairstyle, skin tone, "
                     "build, outfit details), but follow the description below for pose, framing, "
                     "background and lighting.")
    parts.append("Description: " + " ".join((prompt or "").split()))
    return " ".join(parts)


class _BrowserImageEngine:
    name = ""
    family = ""
    site = ""
    label = ""
    runtime = RUNTIME
    default_timeout = 300.0
    caps = ImageCaps(max_refs=0, supports_negative=False, sizes=SIZES)

    def __init__(self, overrides: dict | None = None):
        self.overrides = dict(overrides or {})

    def build(self, req: ImageRequest) -> EngineCall:
        w, h = size_for(self.caps.sizes, req.aspect)
        keep, dropped = req.refs[: self.caps.max_refs], req.refs[self.caps.max_refs:]
        text = web_prompt(req.prompt, req.aspect, len(keep))
        timeout = float(self.overrides.get("timeout") or self.default_timeout)
        fit = bool(self.overrides.get("fit_aspect", True))
        request = {"runner": RUNTIME, "site": self.site, "prompt": text, "aspect": req.aspect,
                   "refs": [str(p) for p in keep], "timeout": timeout, "fit_aspect": fit}
        return EngineCall(self.name, request, "image", self.family,
                          meta={"prompt": req.prompt, "web_prompt": text, "site": self.site,
                                "size": [w, h], "seed": None, "nondeterministic": True,
                                "refs": [str(p) for p in keep],
                                **({"negative_ignored": req.negative} if req.negative else {})},
                          dropped_refs=[str(p) for p in dropped], runner="browser")


class GptImageEngine(_BrowserImageEngine):
    """ChatGPT 网页版出图（GPT 图像模型）。参考图最多 4 张，一张 1–3 分钟。"""
    name = "gpt_image"
    family = "chatgpt_web"
    site = "chatgpt"
    label = "ChatGPT 网页"
    default_timeout = 420.0
    caps = ImageCaps(max_refs=4, supports_negative=False, sizes=SIZES)


class GeminiImageEngine(_BrowserImageEngine):
    """Gemini 网页版出图。参考图最多 3 张，一张几十秒。"""
    name = "gemini_image"
    family = "gemini_web"
    site = "gemini"
    label = "Gemini 网页"
    default_timeout = 300.0
    caps = ImageCaps(max_refs=3, supports_negative=False, sizes=SIZES)


BROWSER_ENGINES = (GptImageEngine, GeminiImageEngine)
# 队列给每个网页引擎族开一条串行通道（不走 GPU worker）
BROWSER_FAMILIES: tuple[str, ...] = tuple(e.family for e in BROWSER_ENGINES)
