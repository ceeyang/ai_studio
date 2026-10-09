"""第一版四个 ComfyUI 引擎。参数与帧数规则移植自 vglib/engines.py（已实测）。"""
from __future__ import annotations

import math
from pathlib import Path

from backend.engines.base import (EngineCall, ImageCaps, ImageRequest, VideoCaps, VideoRequest,
                                  size_for, upload_ref)
from backend.engines.comfyui.template import WorkflowTemplate


class _ComfyEngine:
    name = ""
    family = ""
    template_name = ""

    def __init__(self, tpl: WorkflowTemplate, overrides: dict | None = None):
        self.tpl = tpl
        self.overrides = dict(overrides or {})

    def _render(self, values: dict) -> dict:
        return self.tpl.render({**self.overrides, **values})

    def _setting(self, key: str, default=None):
        return self.overrides.get(key, self.tpl.defaults.get(key, default))


def _add_h3_refs(wf: dict, refs: list[Path], start: int) -> None:
    for i, path in enumerate(refs):
        nid = f"ref_{i}"
        wf[nid] = {"class_type": "LoadImage", "inputs": {"image": upload_ref(path)}}
        wf["6"]["inputs"][f"ref_images.ref_image_{start + i}"] = [nid, 0]


def _add_h3_turbo(wf: dict, lora_name: str) -> None:
    # LoRA 必须同时接到 BasicGuider 和 BasicScheduler，否则 sigma 与模型对不上
    wf["1b"] = {"class_type": "LoraLoaderModelOnly",
                "inputs": {"model": ["1", 0], "strength_model": 1.0, "lora_name": lora_name}}
    wf["7"]["inputs"]["model"] = ["1b", 0]
    wf["9"]["inputs"]["model"] = ["1b", 0]


class ZImageEngine(_ComfyEngine):
    name = "zimage"
    family = "zimage"
    template_name = "zimage"
    caps = ImageCaps(max_refs=0, supports_negative=True,
                     sizes={"9:16": (768, 1344), "16:9": (1344, 768), "1:1": (1024, 1024)})

    def build(self, req: ImageRequest) -> EngineCall:
        w, h = size_for(self.caps.sizes, req.aspect)
        wf = self._render({"prompt": req.prompt, "negative": req.negative, "width": w,
                           "height": h, "seed": req.seed, "prefix": req.prefix})
        return EngineCall(self.name, wf, "image", self.family,
                          meta={"prompt": req.prompt, "negative": req.negative, "seed": req.seed,
                                "size": [w, h]},
                          dropped_refs=[str(p) for p in req.refs])


class H3ImageEngine(_ComfyEngine):
    """H3 ref2va 出 5 帧取第 0 帧。20 步定稿；turbo 4 步只适合探构图（静帧会糊）。"""
    name = "h3_image"
    family = "h3"
    template_name = "h3_image"
    caps = ImageCaps(max_refs=9, supports_negative=False,
                     sizes={"9:16": (768, 1344), "16:9": (1344, 768), "1:1": (960, 960)})

    def build(self, req: ImageRequest) -> EngineCall:
        w, h = size_for(self.caps.sizes, req.aspect)
        turbo = bool(self.overrides.get("turbo", False))
        steps = self.overrides.get("steps") or (4 if turbo else 20)
        keep, dropped = req.refs[: self.caps.max_refs], req.refs[self.caps.max_refs:]
        wf = self._render({"prompt": req.prompt, "width": w // 32 * 32, "height": h // 32 * 32,
                           "seed": req.seed, "steps": steps, "prefix": req.prefix})
        _add_h3_refs(wf, keep, start=0)
        if turbo:
            _add_h3_turbo(wf, self._setting("turbo_lora"))
        return EngineCall(self.name, wf, "image", self.family,
                          meta={"prompt": req.prompt, "seed": req.seed, "size": [w, h],
                                "steps": steps, "turbo": turbo, "refs": [str(p) for p in keep]},
                          dropped_refs=[str(p) for p in dropped])


class H3VideoEngine(_ComfyEngine):
    """H3 参考图 → 视频 + 原生立体声。帧数落在 17k+5 网格（24fps）。"""
    name = "h3_video"
    family = "h3"
    template_name = "h3_video"
    caps = VideoCaps(max_refs=8, supports_end_frame=False, has_audio=True, fps=24,
                     sizes={"9:16": (768, 1344), "16:9": (1344, 768)},
                     default_seconds=5.0, max_seconds=10.0)

    def frames_for(self, seconds: float) -> int:
        """取离目标帧数最近的 17k+5（等距时取长的），最少 5 帧。

        以前一律往上取：4.5s/5s 都出成 124 帧（5.17s）、6s 出成 158 帧（6.58s），成片比分镜长 8%。
        往下取最多短半个网格（17 帧 ÷ 2 ≈ 0.35s），分镜时长本来就是估计值，台词留了 0.4s 余量。
        """
        n = seconds * self.caps.fps
        k = math.floor((n - 5) / 17 + 0.5)
        return max(5, 17 * k + 5)

    def build(self, req: VideoRequest) -> EngineCall:
        w, h = size_for(self.caps.sizes, req.aspect)
        turbo = bool(self.overrides.get("turbo", True))
        steps = self.overrides.get("steps") or (4 if turbo else 20)
        frames = self.frames_for(min(req.seconds, self.caps.max_seconds))
        keep, dropped = req.refs[: self.caps.max_refs], req.refs[self.caps.max_refs:]
        wf = self._render({"prompt": req.prompt, "width": w, "height": h, "frames": frames,
                           "fps": float(self.caps.fps), "seed": req.seed, "steps": steps,
                           "prefix": req.prefix, "first_frame": upload_ref(req.first_frame)})
        _add_h3_refs(wf, keep, start=1)
        if turbo:
            _add_h3_turbo(wf, self._setting("turbo_lora"))
        return EngineCall(self.name, wf, "video", self.family,
                          meta={"prompt": req.prompt, "seed": req.seed, "size": [w, h],
                                "frames": frames, "fps": self.caps.fps, "steps": steps,
                                "turbo": turbo, "first_frame": str(req.first_frame),
                                "refs": [str(p) for p in keep]},
                          dropped_refs=[str(p) for p in dropped])


class Wan22I2VEngine(_ComfyEngine):
    """Wan2.2 I2V 14B + lightx2v 4 步。无声，16fps，帧数为 4k+1。"""
    name = "wan22_i2v"
    family = "wan22"
    template_name = "wan22_i2v"
    caps = VideoCaps(max_refs=0, supports_end_frame=False, has_audio=False, fps=16,
                     sizes={"9:16": (480, 832), "16:9": (832, 480)},
                     default_seconds=5.0, max_seconds=5.0)

    def frames_for(self, seconds: float) -> int:
        n = max(1, round(seconds * self.caps.fps))
        return max(17, (n // 4) * 4 + 1)

    def build(self, req: VideoRequest) -> EngineCall:
        w, h = size_for(self.caps.sizes, req.aspect)
        frames = self.frames_for(min(req.seconds, self.caps.max_seconds))
        values = {"prompt": req.prompt, "first_frame": upload_ref(req.first_frame),
                  "width": w, "height": h, "frames": frames, "seed": req.seed,
                  "fps": float(self.caps.fps), "prefix": req.prefix}
        if req.negative:
            values["negative"] = req.negative
        wf = self._render(values)
        return EngineCall(self.name, wf, "video", self.family,
                          meta={"prompt": req.prompt, "seed": req.seed, "size": [w, h],
                                "frames": frames, "fps": self.caps.fps,
                                "first_frame": str(req.first_frame)},
                          dropped_refs=[str(p) for p in req.refs])
