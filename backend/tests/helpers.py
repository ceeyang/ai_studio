"""测试用的假对象。"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from backend.config import Config, LLMCfg, ProviderCfg, ServerCfg
from backend.domain.media import MediaStore
from backend.domain.models import Episode, Project
from backend.engines.ffmpeg import Assembled, FFmpeg, xfade_timeline
from backend.engines.registry import EngineRegistry
from backend.llm.base import LLMResult
from backend.llm.gateway import LLMGateway
from backend.queue.errors import JobCanceled
from backend.services import Services


class FakeProvider:
    def __init__(self, texts, provider_id: str = "fake"):
        self.id = provider_id
        self.texts = list(texts)
        self.calls: list[dict] = []

    def complete(self, system, messages, *, json_mode=True):
        self.calls.append({"system": system, "messages": list(messages)})
        if not self.texts:
            raise AssertionError("FakeProvider 没有更多预置回复")
        t = self.texts.pop(0)
        if not isinstance(t, str):
            t = json.dumps(t, ensure_ascii=False)
        return LLMResult(text=t, model="fake", usage=None, seconds=0.0)


class FakeCtx:
    def __init__(self, services, *, manager=None, job_id: int = 1):
        self.services = services
        self.manager = manager
        self.job_id = job_id
        self.logs: list[str] = []
        self.progress_calls: list[tuple[int, int]] = []
        self.cancel_event = threading.Event()

    def log(self, msg):
        self.logs.append(msg)

    def progress(self, value, maximum):
        self.progress_calls.append((value, maximum))

    def check_canceled(self):
        if self.cancel_event.is_set():
            raise JobCanceled()


def make_gateway(texts, kind: str = "openai"):
    prov = FakeProvider(texts)
    cfg = LLMCfg(default="fake", providers=[ProviderCfg(id="fake", kind=kind,
                                                        base_url="http://x/v1")])
    return LLMGateway(cfg, {"fake": prov}), prov


def make_services(tmp_path, db, *, llm=None, comfy=None, ffmpeg=None, cfg=None) -> Services:
    cfg = cfg or Config(server=ServerCfg(data_dir=tmp_path / "data"))
    return Services(cfg=cfg, db=db, media=MediaStore(cfg.data_dir), engines=EngineRegistry(cfg),
                    llm=llm, ffmpeg=ffmpeg or FFmpeg(), comfy=comfy)


def seed_episode(db, *, slug="demo", source="林夏在直播间发现提词器被人改了。", target=20.0,
                 image_engine="zimage", video_engine="h3_video"):
    with db.session() as s:
        p = Project(slug=slug, title="提词员", image_engine=image_engine,
                    video_engine=video_engine)
        s.add(p)
        s.flush()
        ep = Episode(project_id=p.id, number=1, source_text=source, target_seconds=target)
        s.add(ep)
        s.flush()
        return p.id, ep.id


class FakeRunner:
    """代替 ComfyRunner：不连 ComfyUI，直接写一个产物文件。"""

    def __init__(self, fail: Exception | None = None):
        self.calls = []
        self.fail = fail

    def run(self, call, *, run_dir, cancel, on_progress=None, timeout=3600.0):
        self.calls.append(call)
        if self.fail:
            raise self.fail
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "workflow.api.json").write_text("{}", encoding="utf-8")
        ext = ".png" if call.output_kind == "image" else ".mp4"
        f = run_dir / f"out_{len(self.calls)}{ext}"
        f.write_bytes(b"x")
        if on_progress:
            on_progress(1, 1)
        return [f]


class FakeFFmpeg:
    """不跑 ffmpeg：每段视频都当 5 秒，合成只写一个占位文件。"""

    ffmpeg = "ffmpeg"

    def __init__(self):
        self.calls: list[dict] = []

    def video_duration(self, path) -> float:
        return 5.0

    def has_audio(self, path) -> bool:
        return True

    def assemble(self, clips, out, *, width, height, fps, transitions, work_dir, max_seconds=None):
        self.calls.append({"clips": list(clips), "out": Path(out), "width": width,
                           "height": height, "fps": fps, "transitions": list(transitions),
                           "max_seconds": list(max_seconds or [])})
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_bytes(b"mp4")
        durs = [min(5.0, m) if m else 5.0 for m in (max_seconds or [None] * len(clips))]
        _, starts, total = xfade_timeline(durs, transitions, fps)
        return Assembled(Path(out), durs, starts, total)
