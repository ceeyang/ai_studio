"""服务容器：处理器和 API 通过它拿到数据库、媒体、引擎、LLM、ffmpeg、ComfyUI、网页出图浏览器。"""
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
    browser: Any = None      # BrowserRunner（gpt_image / gemini_image）
    runninghub: Any = None   # RunningHubRunner（config 里配了 runninghub 才有）
    settings: Any = None     # SettingsStore（设置页的默认模型、按步骤单独设置）
    api: Any = None          # ApiRunner（OpenAI / Gemini 官方 API 的出图、视频引擎）
    api_keys: Any = None     # ApiKeyStore（设置页「API 服务」：Key、Base URL、模型名）

    def prompts(self, slug: str) -> PromptLibrary:
        return PromptLibrary(BUILTIN_DIR, self.media.project_dir(slug) / "prompts")


def build_services(cfg: Config, db: Database, manager: Any) -> Services:
    from backend.domain.api_keys import ApiKeyStore
    from backend.engines.api.engines import API_FAMILIES
    from backend.engines.api.runner import ApiRunner
    from backend.engines.browser.engines import BROWSER_FAMILIES
    from backend.engines.browser.runner import BrowserRunner
    from backend.engines.browser.session import BrowserSession
    from backend.engines.comfyui.client import ComfyClient
    from backend.engines.comfyui.runner import ComfyRunner
    from backend.engines.runninghub.client import RunningHubClient
    from backend.engines.runninghub.engines import FAMILY as RH_FAMILY
    from backend.engines.runninghub.runner import RunningHubRunner
    from backend.llm.api_providers import ensure_api_providers
    from backend.llm.gateway import LLMGateway
    from backend.llm.providers import build_providers
    from backend.llm.web_providers import ensure_web_providers

    client = ComfyClient(cfg.comfyui.url, timeout=cfg.comfyui.timeout,
                         max_retries=cfg.comfyui.max_retries)
    runner = ComfyRunner(client)
    from backend.domain.settings import SettingsStore

    store = SettingsStore(db)
    keys = ApiKeyStore(db)
    # OpenAI / Anthropic / Gemini 的 API 文本模型：config.yaml 没写也加上（没配 Key 时设置页里不可选）
    ensure_api_providers(cfg.llm)
    # ChatGPT / Gemini / Claude / Muse 网页文本模型（走浏览器扩展）：config.yaml 没写也加上（扩展没连上时设置页里不可选）
    ensure_web_providers(cfg.llm)
    browser = BrowserRunner(BrowserSession(cfg.browser), ffprobe=cfg.ffmpeg.ffprobe)
    # 重新生成时续接之前的网页会话（按角色 / 镜头 / 场景 / 道具 / 某集某一步），设置页可关
    from backend.engines.browser.conversations import ConversationBook
    browser.conversations = ConversationBook(db, store)
    # 每次调用 LLM 时现读设置：按步骤单独设置 → 全局默认文本模型 → config.yaml
    llm = LLMGateway(cfg.llm, build_providers(cfg.llm, creds=keys.resolve, browser=browser),
                     gpu_guard=manager.gpu_exclusive, override=store.llm_provider)
    # 换模型族、或本机 LLM 要用显卡时，先让 ComfyUI 把模型卸掉
    manager.on_family_switch = lambda old, new: runner.free()
    manager.on_gpu_handover = runner.free

    def on_gpu_idle() -> None:
        runner.free()
        log.info("队列空闲超过 %.0f 秒没有 GPU 任务，已自动释放 ComfyUI 显存", cfg.comfyui.idle_free_seconds)
        manager.bus.publish({"type": "queue", "event": "gpu_idle_freed", **manager.queue_state()})

    manager.idle_free_seconds = cfg.comfyui.idle_free_seconds
    manager.on_gpu_idle = on_gpu_idle
    # 网页出图每个网站一条串行通道，不占 GPU worker、不触发换模型族/释放显存
    # 官方 API 的出图 / 视频引擎也不占显卡：每个引擎族一条串行通道
    manager.lanes = (*BROWSER_FAMILIES, *API_FAMILIES)
    rh = None
    if cfg.runninghub is not None:
        # 云端任务不占本机显卡：单独一条串行通道
        manager.lanes = (*BROWSER_FAMILIES, *API_FAMILIES, RH_FAMILY)
        rh = RunningHubRunner(RunningHubClient(cfg.runninghub.base_url, cfg.runninghub.api_key(),
                                               api_key_env=cfg.runninghub.api_key_env,
                                               poll_interval=cfg.runninghub.poll_interval))
    return Services(cfg=cfg, db=db, media=MediaStore(cfg.data_dir),
                    engines=EngineRegistry(cfg, api_creds=keys.resolve),
                    llm=llm, ffmpeg=FFmpeg(cfg.ffmpeg.ffmpeg, cfg.ffmpeg.ffprobe),
                    comfy=runner, comfy_client=client,
                    browser=browser, runninghub=rh, settings=store,
                    api=ApiRunner(keys.resolve), api_keys=keys)
