"""官方 API 的出图 / 视频引擎：OpenAI 图像（gpt-image-1）、Gemini 图像（gemini-2.5-flash-image）、
Google Veo（Gemini API）、OpenAI Sora、Seedance（火山方舟 / BytePlus）、可灵（可灵开放平台）。

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
from backend.engines.base import (EngineCall, require_first_frame, ImageCaps, ImageRequest, VideoCaps, VideoRequest,
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
    # 参考图只能在没有首帧时用（参考图直出），不能和首帧 / 尾帧一起发
    refs_exclusive = False

    def frames_for(self, seconds: float) -> int:
        return max(1, round(seconds * self.caps.fps))

    def _request(self, req: VideoRequest, seconds: int | None) -> dict:  # pragma: no cover
        raise NotImplementedError

    def _seconds(self, req: VideoRequest, model: str) -> int | None:  # pragma: no cover
        raise NotImplementedError

    def build(self, req: VideoRequest) -> EngineCall:
        """尾帧：caps.supports_end_frame 时带上（workflow 里有 end_frame 键），否则记进 dropped_refs；
        参考图：caps.max_refs > 0 时最多带 max_refs 张（D3 参考图直出用），其余和不支持时都记进 dropped_refs；
        refs_exclusive 的引擎（Seedance）有首帧时参考图一律不带。没有首帧（参考图直出）时尾帧也不带。
        Veo / Sora 两个都是 0 / False，行为和以前一样（多了一条：给了尾帧会记成没带上）。"""
        require_first_frame(req, self)   # 没有首帧（参考图直出）而引擎不支持 ref_mode 时报清楚
        w, h = size_for(self.caps.sizes, req.aspect)
        model = self.model()
        seconds = self._seconds(req, model)
        out_seconds = float(seconds or self.caps.default_seconds)
        first = str(req.first_frame) if req.first_frame is not None else None
        # refs_exclusive：参考图和首尾帧不能一起发（Seedance 三种输入方式互斥）→ 有首帧时参考图一律不带
        take_refs = self.caps.max_refs > 0 and not (self.refs_exclusive and first is not None)
        keep = list(req.refs[: self.caps.max_refs]) if take_refs else []
        dropped = [str(p) for p in req.refs[len(keep):]]
        # 没有首帧（参考图直出）时也不带尾帧：尾帧要跟首帧一起
        end = req.end_frame if (req.end_frame and self.caps.supports_end_frame and first is not None) else None
        if req.end_frame and end is None:
            dropped.append(str(req.end_frame))
        request = {"runner": RUNTIME, "service": self.service, "op": "video", "model": model,
                   "prompt": " ".join((req.prompt or "").split()), "aspect": req.aspect,
                   "size": f"{w}x{h}", "first_frame": first, "seconds": seconds,
                   "timeout": self.timeout()}
        if end is not None:
            request["end_frame"] = str(end)
        if keep:
            request["refs"] = [str(p) for p in keep]
        request.update(self._request(req, seconds))
        meta = {"prompt": req.prompt, "service": self.service, "model": model, "seed": None,
                "nondeterministic": True, "size": [w, h], "seconds": out_seconds,
                "frames": self.frames_for(out_seconds), "fps": self.caps.fps,
                "first_frame": first, "refs": [str(p) for p in keep]}
        if end is not None:
            meta["end_frame"] = str(end)
        if req.negative and "negative" not in request:
            meta["negative_ignored"] = req.negative
        meta.update(self._meta(request))
        return EngineCall(self.name, request, "video", self.family, meta=meta, dropped_refs=dropped,
                          runner="api")

    def _meta(self, request: dict) -> dict:
        """子类往 meta 里补的（比如可灵为了尾帧改成 pro 模式）。"""
        return {}


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


def _ceil_seconds(want: float, lo: int, hi: int) -> int:
    """要的秒数往上取整到整秒（台词不能被截），夹在 [lo, hi]。"""
    n = int(want) if abs(want - round(want)) < 1e-6 else int(want) + 1
    return max(lo, min(hi, n))


# ---------------------------------------------------------------- Seedance（火山方舟 / BytePlus ModelArk）
# 720p 输出尺寸：9:16 = 720×1280、16:9 = 1280×720（第三方实测报告，2026-10）；1:1 按 1.x 文档的 960×960（未实测）
SEEDANCE_SIZES = {"9:16": (720, 1280), "16:9": (1280, 720), "1:1": (960, 960)}
SEEDANCE_SECONDS = tuple(range(4, 16))       # 2.0 / 2.5：4–15 秒整数


def seedance_seconds_range(model: str) -> tuple[int, int]:
    """各代 Seedance 的 duration 范围：2.x 是 4–15；1.5 pro 是 4–12；1.0 是 2–12。"""
    if "seedance-2" in model:
        return 4, 15
    if "seedance-1-5" in model:
        return 4, 12
    return 2, 12


def seedance_has_audio(model: str) -> bool:
    """generate_audio 只有 1.5 pro 和 2.x 认（1.0 没有声音，不传这个参数）。"""
    return "seedance-2" in model or "seedance-1-5" in model


class SeedanceEngine(_ApiVideoEngine):
    """Seedance（火山方舟 Ark / 海外 BytePlus ModelArk）：首帧（+ 尾帧）图生视频，有声。

    接口（2026-10-12 核对；官方文档页是 JS 渲染的，抓不到原文，以下来自第三方转述的 Ark 原生格式和 SDK 示例，
    未用真实 Key 跑过，见 docs/specs/2026-10-12-seedance-kling-video-design.md §4）：
    - POST {base}/contents/generations/tasks，Bearer API Key。body：model、content（[{type: text, text},
      {type: image_url, image_url: {url: data URI}, role: first_frame}, {…, role: last_frame}]）、
      ratio（16:9 / 9:16 / 1:1 / 4:3 / 3:4 / 21:9 / adaptive）、duration（2.x：4–15 整数秒）、
      resolution（480p / 720p；1080p 只有 2.0 标准版和 2.5，fast / mini 最高 720p）、generate_audio（默认 true）、
      watermark（默认 false，这里明确传 false）；
    - 返回 {id}；GET {base}/contents/generations/tasks/{id}：status = queued / running / succeeded / failed /
      expired，成功时 content.video_url（24 小时有效，拿到马上下载），失败时 error.{code, message}；
    - 模型 id：doubao-seedance-2-0-260128（默认）、doubao-seedance-2-0-fast-260128、doubao-seedance-2-5-260628、
      doubao-seedance-1-5-pro-251215；mini 版 id 要在控制台确认。用之前要在控制台「开通管理」里开通。
    - 输出 24fps；没有负面提示词（丢掉，meta 记 negative_ignored）；有 seed 参数但这里不传（同一 seed 也不保证一样）。

    参考图直出（多模态参考，2026-10-12 核对：apiyi 的 Seedance 原生接口文档、DataCamp 用 BytePlus Ark SDK 的教程
    两处一致）：content 里每张图 {type: image_url, image_url: {url}, role: reference_image}，2.0 系列 1–9 张
    （2.5 更多，这里按 9 张）；三种输入方式互斥——首帧、首尾帧、多模态参考不能混用，所以有首帧时不带参考图
    （refs_exclusive），没有首帧时只发参考图、不发尾帧。2.0 系列对真人人脸参考图有限制（真人照片可能被拒），
    1.x 模型不支持多模态参考（换 1.x 时别用参考图直出）。未用真实 Key 跑过。

    caps 按默认模型（2.0）写：时长 4–15 秒、有声、参考图直出最多 9 张。换成 1.x 模型时 _seconds 按那一代的范围夹，
    1.0 不传 generate_audio。
    params：resolution（默认 720p）、generate_audio（默认 true）。
    """
    name = "seedance_video"
    family = "seedance_api"
    label = "Seedance（火山方舟 API）"
    service = "ark"
    refs_exclusive = True
    caps = VideoCaps(max_refs=9, supports_end_frame=True, has_audio=True, fps=24, sizes=SEEDANCE_SIZES,
                     default_seconds=5.0, max_seconds=15.0, duration_control="param",
                     allowed_seconds=SEEDANCE_SECONDS, ref_mode=True)

    def _seconds(self, req, model) -> int:
        lo, hi = seedance_seconds_range(model)
        return _ceil_seconds(max(req.seconds, req.min_seconds), lo, hi)

    def _request(self, req, seconds) -> dict:
        model = self.model()
        audio = bool(self.overrides.get("generate_audio", True)) if seedance_has_audio(model) else None
        return {"resolution": str(self.overrides.get("resolution") or "720p"), "generate_audio": audio}


# ---------------------------------------------------------------- 可灵（可灵开放平台）
# std 模式 720p、pro 模式 1080p；图生视频的画幅跟着首帧走（接口没有 aspect_ratio），首帧已经是要的画幅。
# 1:1 的尺寸未实测（按 720 / 1080 方形写）
KLING_SIZES = {"9:16": (720, 1280), "16:9": (1280, 720), "1:1": (720, 720)}
KLING_PRO_SIZES = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080)}
KLING_V3_SECONDS = tuple(range(3, 16))       # kling-v3：3–15 秒
KLING_OLD_SECONDS = (5, 10)                   # v2.x 及更早：只有 "5" / "10"
KLING_PROMPT_MAX = 2500                       # 官方文档：prompt / negative_prompt 不超过 2500 字符


def kling_is_v3(model: str) -> bool:
    return model.startswith("kling-v3")


def kling_has_audio(model: str) -> bool:
    """sound 参数（on / off）只有 kling-v2-6 和 v3 认，更早的不传。"""
    return kling_is_v3(model) or model.startswith("kling-v2-6")


class KlingEngine(_ApiVideoEngine):
    """可灵开放平台 API：首帧（+ 尾帧）图生视频，有声（v2.6 / v3）。

    接口（2026-10-12 核对；官方文档 app.klingai.com / kling.ai 的 document-api 是 JS 渲染的，抓不到原文，
    以下来自第三方转述的官方字段表、ComfyUI 官方节点文档和公开 SDK，未用真实 Key 跑过）：
    - 地址：国内 https://api-beijing.klingai.com，海外 https://api-singapore.klingai.com；
    - 认证：API Key（Bearer），或 AccessKey + SecretKey 签 HS256 JWT（iss=AK，exp=now+1800，nbf=now-5），
      每个请求现签（backend/llm/api_http.py）；
    - POST /v1/videos/image2video：model_name、image（base64，不带 data: 前缀）、image_tail（尾帧，同样）、
      prompt、negative_prompt（各 ≤ 2500 字符）、mode（std / pro）、duration（字符串；v3 是 "3"–"15"，
      v2.x 及更早只有 "5" / "10"）、sound（"on" / "off"，v2.6 / v3）、cfg_scale（0–1，v2.x 不支持，只在 params
      里写了才传）。图生视频没有 aspect_ratio（画幅跟首帧走），所以不传；
    - 返回 {code: 0, data: {task_id, task_status}}；GET /v1/videos/image2video/{task_id}：task_status =
      submitted / processing / succeed / failed，失败原因在 task_status_msg，成功时 data.task_result.videos[0].url；
      业务错误是 HTTP 4xx/200 + 顶层 code（1101 欠费、1102 资源包用完、1301 内容审核…），见 api_http.KLING_HINTS；
    - 尾帧（image_tail）：老文档写明只有 pro 模式支持（v1.x 的 std 有个别例外），v2.1 起的首尾帧也都是 pro；
      v3 std 是否支持没查到确切说法。所以带尾帧时一律用 pro 模式（meta 里记 mode_forced），宁可贵一点也不失败。
    - 模型：kling-v3（默认）、kling-v2-6、kling-v2-5-turbo、kling-v2-1。输出 24fps（未实测）。

    caps 按默认模型（v3）写：时长 3–15 秒、有声、std 720p。
    params：mode（默认 std）、sound（默认 true）、cfg_scale（默认不传）。
    """
    name = "kling_video"
    family = "kling_api"
    label = "可灵（API）"
    service = "kling"
    caps = VideoCaps(max_refs=0, supports_end_frame=True, has_audio=True, fps=24, sizes=KLING_SIZES,
                     default_seconds=5.0, max_seconds=15.0, duration_control="param",
                     allowed_seconds=KLING_V3_SECONDS)

    def _seconds(self, req, model) -> int:
        want = max(req.seconds, req.min_seconds)
        if kling_is_v3(model):
            return _ceil_seconds(want, 3, 15)
        return next((s for s in KLING_OLD_SECONDS if s >= want - 1e-6), KLING_OLD_SECONDS[-1])

    def _request(self, req, seconds) -> dict:
        model = self.model()
        mode = str(self.overrides.get("mode") or "std")
        out: dict = {"mode": mode,
                     "negative": (req.negative or "")[:KLING_PROMPT_MAX] or None,
                     "sound": (("on" if self.overrides.get("sound", True) else "off")
                               if kling_has_audio(model) else None)}
        if req.end_frame and mode != "pro":
            out["mode"] = "pro"
            out["mode_forced"] = f"带尾帧要 pro 模式（设置的是 {mode}）"
        if self.overrides.get("cfg_scale") is not None:
            out["cfg_scale"] = float(self.overrides["cfg_scale"])
        return out

    def _meta(self, request: dict) -> dict:
        out: dict = {"mode": request["mode"]}
        if request.get("mode_forced"):
            out["mode_forced"] = request["mode_forced"]
        if request["mode"] == "pro":
            w, h = size_for(KLING_PRO_SIZES, request["aspect"])
            out["size"] = [w, h]
        return out


API_ENGINES = (OpenAIImageEngine, GeminiApiImageEngine, VeoEngine, SoraEngine, SeedanceEngine, KlingEngine)
# 队列给每个 API 引擎族开一条串行通道（不走 GPU worker）
API_FAMILIES: tuple[str, ...] = tuple(e.family for e in API_ENGINES)
