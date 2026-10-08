"""测试用的假对象。"""
from __future__ import annotations

import json
import threading

from backend.config import Config, LLMCfg, ProviderCfg, ServerCfg
from backend.domain.media import MediaStore
from backend.domain.models import Episode, Project
from backend.engines.ffmpeg import FFmpeg
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
