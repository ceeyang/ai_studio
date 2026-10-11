"""网页引擎：出图 gpt_image（chatgpt.com）、gemini_image（gemini.google.com）、muse_image（muse.ai）；
出视频 gemini_video、muse_video（聊天页），jimeng_video（即梦 / Dreamina）、kling_web_video（可灵）
（生成器页，见 _GeneratorVideoEngine），都只走浏览器扩展。

build() 只把请求拼成「网页出图请求」（EngineCall.runner="browser"，workflow 里是网站、提示词、
参考图、画幅），执行交给 BrowserRunner。网页没有尺寸、种子、负面提示词参数：画幅和尺寸写进提示词，
结果每次不同（没有种子），负面提示词丢掉。出图后按画幅居中裁切（params.fit_aspect，默认开）。

出视频：首帧图当参考图传上去，提示词写明「用附图做第一帧」；画幅 Gemini 在页面菜单里选、Muse 写进提示词；
时长见各引擎的 caps（duration_control / allowed_seconds，2026-10-11 实测）。视频比镜头长时剪辑按镜头时长截。

params（config.yaml engines.<name>.params）：
  timeout: 等结果的最长秒数（gpt 出图默认 420，其余出图 300；出视频 gemini 900、muse 600）
  fit_aspect: 出来的图宽高比不对时居中裁成要求的画幅（默认 true；视频不裁）
"""
from __future__ import annotations

from backend.engines.base import EngineCall, require_first_frame, ImageCaps, ImageRequest, VideoCaps, VideoRequest, size_for

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


class MuseImageEngine(_BrowserImageEngine):
    """Meta Muse 网页版出图（muse.ai side chat）。约 25 秒一张，画幅按提示词出得很准（9:16 正好 1152×2048）。"""
    name = "muse_image"
    family = "muse_web"
    site = "muse"
    label = "Muse 网页"
    default_timeout = 300.0
    caps = ImageCaps(max_refs=3, supports_negative=False, sizes=SIZES)


# ---------------------------------------------------------------- 出视频
VIDEO_ASPECT_TEXT = {
    "9:16": "vertical 9:16 portrait (tall video)",
    "16:9": "horizontal 16:9 landscape (wide video)",
}


def web_video_prompt(prompt: str, aspect: str, seconds: float | None, first_frame: bool) -> str:
    """一行写完（不能有换行）。seconds 为 None 时不写时长（网站不认）。"""
    parts = ["Generate one video now using your video generation tool.",
             "Do not ask any questions and do not answer with text only."]
    if first_frame:
        parts.append("Use the attached image exactly as the first frame of the video: keep the same people, "
                     "framing, colors and lighting, and animate from it.")
    fmt = f"Video format: {VIDEO_ASPECT_TEXT[aspect]}"
    if seconds:
        fmt += f", exactly {seconds:g} seconds long"
    parts.append(fmt + ". No subtitles, no text overlays, no watermark.")
    parts.append("Description: " + " ".join((prompt or "").split()))
    return " ".join(parts)


class _BrowserVideoEngine:
    name = ""
    family = ""
    site = ""
    label = ""
    runtime = RUNTIME
    default_timeout = 600.0
    caps = VideoCaps(max_refs=0, supports_end_frame=False, has_audio=True, fps=24,
                     sizes={"9:16": (720, 1280), "16:9": (1280, 720)}, default_seconds=10.0, max_seconds=10.0,
                     duration_control="fixed")

    def __init__(self, overrides: dict | None = None):
        self.overrides = dict(overrides or {})

    def frames_for(self, seconds: float) -> int:
        return max(1, round(seconds * self.caps.fps))

    def ask_seconds(self, req: VideoRequest) -> float | None:
        """写进提示词的时长：fixed 不写；prompt 取至少「镜头时长、台词说完」的最近一档（不比镜头短）。"""
        c = self.caps
        if c.duration_control == "fixed":
            return None
        want = max(req.seconds, req.min_seconds)
        if c.allowed_seconds:
            return float(next((x for x in c.allowed_seconds if x >= want - 1e-6), c.allowed_seconds[-1]))
        return float(min(max(want, 1.0), c.max_seconds))

    def build(self, req: VideoRequest) -> EngineCall:
        require_first_frame(req, self)   # 没有首帧（参考图直出）而引擎不支持 ref_mode 时报清楚
        w, h = size_for(self.caps.sizes, req.aspect)
        ask = self.ask_seconds(req)
        out_seconds = float(ask or self.caps.default_seconds)
        text = web_video_prompt(req.prompt, req.aspect, ask, first_frame=True)
        timeout = float(self.overrides.get("timeout") or self.default_timeout)
        request = {"runner": RUNTIME, "site": self.site, "kind": "video", "prompt": text, "aspect": req.aspect,
                   "refs": [str(req.first_frame)], "timeout": timeout, "fit_aspect": False}
        meta = {"prompt": req.prompt, "web_prompt": text, "site": self.site, "size": [w, h], "seed": None,
                "nondeterministic": True, "seconds": out_seconds, "frames": self.frames_for(out_seconds),
                "fps": self.caps.fps, "first_frame": str(req.first_frame), "refs": [],
                "duration_control": self.caps.duration_control, "asked_seconds": ask,
                "shot_seconds": req.seconds}
        if req.negative:
            meta["negative_ignored"] = req.negative
        return EngineCall(self.name, request, "video", self.family, meta=meta,
                          dropped_refs=[str(p) for p in req.refs], runner="browser")


class GeminiVideoEngine(_BrowserVideoEngine):
    """Gemini 网页版出视频（Veo，「Create video」工具）：首帧图生视频，有声，720×1280 / 1280×720，24 fps。
    时长不能选（2026-10-11 实测要 4 秒也给 10 秒），一段约 3 分钟；要 Google AI Pro / Ultra，有每日次数限制。"""
    name = "gemini_video"
    family = "gemini_web"
    site = "gemini"
    label = "Gemini 网页（Veo）"
    default_timeout = 900.0
    caps = VideoCaps(max_refs=0, supports_end_frame=False, has_audio=True, fps=24,
                     sizes={"9:16": (720, 1280), "16:9": (1280, 720)}, default_seconds=10.0, max_seconds=10.0,
                     duration_control="fixed", allowed_seconds=(10.0,))


class MuseVideoEngine(_BrowserVideoEngine):
    """Meta Muse 网页版出视频：首帧图生视频，有声，704×1248，24 fps，约 1 分钟一段。时长只能写进提示词，
    2026-10-11 实测只认 3 / 5 / 10 秒几档且不一定照做（要 3 秒给 3 秒，要 5 秒有时给 10 秒，要 8 秒给 10 秒）。"""
    name = "muse_video"
    family = "muse_web"
    site = "muse"
    label = "Muse 网页"
    default_timeout = 600.0
    caps = VideoCaps(max_refs=0, supports_end_frame=False, has_audio=True, fps=24,
                     sizes={"9:16": (704, 1248), "16:9": (1248, 704)}, default_seconds=10.0, max_seconds=10.0,
                     duration_control="prompt", allowed_seconds=(3.0, 5.0, 10.0))


# ---------------------------------------------------------------- 生成器页出视频（即梦 / 可灵）
def generator_prompt(prompt: str) -> str:
    """生成器页的提示词：就是镜头描述，一行（画幅、时长在页面上选，不用写进提示词）。"""
    return " ".join((prompt or "").split())


class _GeneratorVideoEngine(_BrowserVideoEngine):
    """即梦 / 可灵这类生成器页（不是聊天页）：模型、参考方式、画幅、时长都在页面上选，首帧（和尾帧）传进上传位。
    流程和选择器在扩展里（browser-extension/lib/flow.js 的 runGenerator、sites/jimeng.js、kling.js），要扩展
    报了 generator 能力（v0.5 起），老扩展在引擎下拉里提示「扩展需要重新加载」。

    区域（国内 / 海外）：设置页「网页出图」里选，存 app_settings 的 web_region_<family>，默认国内；
    params.region 能在 config.yaml 里写死。build() 按区域选站点（jimeng_cn / jimeng_global …）。

    params（config.yaml engines.<name>.params）：
      timeout: 等结果的最长秒数
      model: 页面上模型下拉要选的（按名字开头匹配，如「Seedance 2.0」）；不写用 default_model，null 不动页面上的
      mode: 参考方式 / 生成方式（即梦「首尾帧」、可灵「图生视频」）；不写用扩展里站点的默认
      region: cn / global（写了就不看设置页）
    """
    site_family = ""
    ext_feature = "generator"
    default_model: str | None = None
    default_timeout = 1200.0

    def __init__(self, overrides: dict | None = None, region=None):
        super().__init__(overrides)
        # 现读区域设置的函数（build_services 里装上 SettingsStore.web_region）；没有就国内
        self.region_source = region

    def set_region_source(self, fn) -> None:
        self.region_source = fn

    def region(self) -> str:
        from backend.engines.browser.sites.generators import REGIONS

        r = self.overrides.get("region")
        if not r and self.region_source is not None:
            try:
                r = self.region_source(self.site_family)
            except Exception:
                r = None
        return r if r in REGIONS else "cn"

    def site_key(self) -> str:
        from backend.engines.browser.sites.generators import generator_site

        return generator_site(self.site_family, self.region()).key

    def build(self, req: VideoRequest) -> EngineCall:
        from backend.engines.browser.sites.generators import GENERATOR_SITES

        require_first_frame(req, self)   # 没有首帧（参考图直出）而引擎不支持 ref_mode 时报清楚
        w, h = size_for(self.caps.sizes, req.aspect)
        ask = self.ask_seconds(req)
        seconds = float(ask or self.caps.default_seconds)
        site = self.site_key()
        text = generator_prompt(req.prompt)
        timeout = float(self.overrides.get("timeout") or self.default_timeout)
        end = req.end_frame if (req.end_frame and self.caps.supports_end_frame) else None
        refs = [str(req.first_frame)] + ([str(end)] if end else [])
        model = self.overrides.get("model", self.default_model)
        choices = {"model": model or None, "mode": self.overrides.get("mode") or None, "ratio": req.aspect,
                   "duration": int(round(seconds)), "end_frame": bool(end)}
        request = {"runner": RUNTIME, "site": site, "kind": "video", "generator": True, "choices": choices,
                   "prompt": text, "aspect": req.aspect, "refs": refs, "timeout": timeout, "fit_aspect": False}
        meta = {"prompt": req.prompt, "web_prompt": text, "site": site, "region": GENERATOR_SITES[site].region,
                "size": [w, h], "seed": None, "nondeterministic": True, "seconds": seconds,
                "frames": self.frames_for(seconds), "fps": self.caps.fps, "first_frame": str(req.first_frame),
                "end_frame": str(end) if end else None, "refs": [], "choices": choices,
                "duration_control": self.caps.duration_control, "asked_seconds": ask, "shot_seconds": req.seconds}
        if req.negative:
            meta["negative_ignored"] = req.negative
        dropped = [str(p) for p in req.refs]
        if req.end_frame and not end:
            dropped.append(str(req.end_frame))
        return EngineCall(self.name, request, "video", self.family, meta=meta, dropped_refs=dropped, runner="browser")


class JimengVideoEngine(_GeneratorVideoEngine):
    """即梦（国内 jimeng.jianying.com）/ Dreamina（海外 dreamina.capcut.com）网页出视频，Seedance 模型。
    「首尾帧」参考方式：首帧 + 可选尾帧。页面时长能填 4–15 秒的整数，这里按 5 / 10 / 15 三档取（不比镜头短）。
    有声、24 fps、720P 尺寸是按 Seedance 2.0 的公开说明填的，待实跑确认（docs/notes/2026-10-12-jimeng-kling-web.md）。
    默认模型 Seedance 2.0 mini（页面默认、最便宜：5 秒 720P 30 积分）；2.0 系列暂不支持真人人脸参考图。"""
    name = "jimeng_video"
    family = "jimeng_web"
    site = "jimeng_cn"
    site_family = "jimeng"
    label = "即梦网页（Seedance）"
    default_model = "Seedance 2.0 mini"
    caps = VideoCaps(max_refs=0, supports_end_frame=True, has_audio=True, fps=24,
                     sizes={"9:16": (720, 1280), "16:9": (1280, 720)}, default_seconds=5.0, max_seconds=15.0,
                     duration_control="param", allowed_seconds=(5.0, 10.0, 15.0))


class KlingWebVideoEngine(_GeneratorVideoEngine):
    """可灵网页出视频（国内 klingai.com / 国际 kling.ai），图生视频：首帧，5 / 10 秒。
    尾帧上传位还没实测到，先不支持；有声（可灵 3.0）和 1080p 尺寸待实跑确认。模型不写就用页面上现在选的。"""
    name = "kling_web_video"
    family = "kling_web"
    site = "kling_cn"
    site_family = "kling"
    label = "可灵网页"
    default_model = None
    caps = VideoCaps(max_refs=0, supports_end_frame=False, has_audio=True, fps=24,
                     sizes={"9:16": (1080, 1920), "16:9": (1920, 1080)}, default_seconds=5.0, max_seconds=10.0,
                     duration_control="param", allowed_seconds=(5.0, 10.0))


BROWSER_IMAGE_ENGINES = (GptImageEngine, GeminiImageEngine, MuseImageEngine)
BROWSER_VIDEO_ENGINES = (GeminiVideoEngine, MuseVideoEngine, JimengVideoEngine, KlingWebVideoEngine)
GENERATOR_ENGINES = (JimengVideoEngine, KlingWebVideoEngine)
BROWSER_ENGINES = (*BROWSER_IMAGE_ENGINES, *BROWSER_VIDEO_ENGINES)
# 队列给每个网页引擎族开一条串行通道（不走 GPU worker）
BROWSER_FAMILIES: tuple[str, ...] = tuple(dict.fromkeys(e.family for e in BROWSER_ENGINES))
