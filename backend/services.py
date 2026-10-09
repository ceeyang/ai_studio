"""服务容器：处理器和 API 通过它拿到数据库、媒体、引擎、LLM、ffmpeg、ComfyUI。"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from backend.config import Config
from backend.domain.db import Database
from backend.domain.media import MediaStore
from backend.engines.ffmpeg import FFmpeg
from backend.engines.registry import EngineRegistry
from backend.llm.prompts import BUILTIN_DIR, PromptLibrary

log = logging.getLogger(__name__)


@dataclass
class Services:
    cfg: Config
    db: Database
    media: MediaStore
    engines: EngineRegistry
    llm: Any                 # LLMGateway
    ffmpeg: FFmpeg
    comfy: Any = None        # ComfyRunner
    comfy_client: Any = None  # ComfyClient

    def prompts(self, slug: str) -> PromptLibrary:
        return PromptLibrary(BUILTIN_DIR, self.media.project_dir(slug) / "prompts")


def build_services(cfg: Config, db: Database, manager: Any) -> Services:
    from backend.engines.comfyui.client import ComfyClient
    from backend.engines.comfyui.runner import ComfyRunner
    from backend.llm.gateway import LLMGateway
    from backend.llm.providers import build_providers

    client = ComfyClient(cfg.comfyui.url, timeout=cfg.comfyui.timeout,
                         max_retries=cfg.comfyui.max_retries)
    runner = ComfyRunner(client)
    llm = LLMGateway(cfg.llm, build_providers(cfg.llm), gpu_guard=manager.gpu_exclusive)
    # 换模型族、或本机 LLM 要用显卡时，先让 ComfyUI 把模型卸掉
    manager.on_family_switch = lambda old, new: runner.free()
    manager.on_gpu_handover = runner.free

    def on_gpu_idle() -> None:
        runner.free()
        log.info("队列空闲超过 %.0f 秒没有 GPU 任务，已自动释放 ComfyUI 显存", cfg.comfyui.idle_free_seconds)
        manager.bus.publish({"type": "queue", "event": "gpu_idle_freed", **manager.queue_state()})

    manager.idle_free_seconds = cfg.comfyui.idle_free_seconds
    manager.on_gpu_idle = on_gpu_idle
    return Services(cfg=cfg, db=db, media=MediaStore(cfg.data_dir), engines=EngineRegistry(cfg),
                    llm=llm, ffmpeg=FFmpeg(cfg.ffmpeg.ffmpeg, cfg.ffmpeg.ffprobe),
                    comfy=runner, comfy_client=client)
