"""测试用的视频引擎：支持参考图直出（caps.ref_mode），不用首帧也能出。执行走 FakeRunner（comfyui 运行方式）。"""
from __future__ import annotations

from backend.engines.base import EngineCall, VideoCaps, VideoRequest, require_first_frame, size_for


class FakeRefVideoEngine:
    name = "fake_refs_video"
    label = "假参考图直出视频"
    family = "fake_refs"
    runtime = "comfyui"

    def __init__(self, max_refs: int = 3, ref_mode: bool = True):
        self.caps = VideoCaps(max_refs=max_refs, supports_end_frame=False, has_audio=True, fps=24,
                              sizes={"9:16": (720, 1280), "16:9": (1280, 720)},
                              default_seconds=5.0, max_seconds=10.0, ref_mode=ref_mode)
        self.requests: list[VideoRequest] = []

    def frames_for(self, seconds: float) -> int:
        return max(1, round(seconds * self.caps.fps))

    def build(self, req: VideoRequest) -> EngineCall:
        require_first_frame(req, self)
        self.requests.append(req)
        w, h = size_for(self.caps.sizes, req.aspect)
        keep, dropped = req.refs[: self.caps.max_refs], req.refs[self.caps.max_refs:]
        first = str(req.first_frame) if req.first_frame else None
        wf = {"prompt": req.prompt, "first_frame": first, "refs": [str(p) for p in keep]}
        return EngineCall(self.name, wf, "video", self.family,
                          meta={"prompt": req.prompt, "seed": req.seed, "size": [w, h],
                                "first_frame": first, "refs": [str(p) for p in keep]},
                          dropped_refs=[str(p) for p in dropped])


def install(sv, **kw) -> FakeRefVideoEngine:
    """把假引擎装进服务的引擎表（EngineRegistry 没有公开的注册入口，测试直接塞）。"""
    eng = FakeRefVideoEngine(**kw)
    sv.engines._engines[eng.name] = eng
    return eng
