"""服务容器：处理器和 API 通过它拿到数据库、媒体、引擎、LLM、ffmpeg、ComfyUI。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.config import Config
from backend.domain.db import Database
from backend.domain.media import MediaStore
from backend.engines.ffmpeg import FFmpeg
from backend.engines.registry import EngineRegistry
from backend.llm.prompts import BUILTIN_DIR, PromptLibrary


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
