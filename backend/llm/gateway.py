"""LLM 网关：按任务路由 provider，解析并校验 JSON，不合格就带着错误重试。

本机 GPU 上的 provider（LM Studio）在 gpu_guard 里调用：先等 GPU 任务让出显卡，
调用结束后卸载模型，再把显卡还给 ComfyUI。

用哪个 provider：每次调用时先问 override(step)（设置页的「按步骤单独设置 → 默认文本模型」，
见 backend/domain/settings.py），没有再按 config.yaml 的 llm.routes / llm.default。改设置不用重启。
"""
from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from typing import Any, TypeVar

from pydantic import BaseModel

from backend.config import LLMCfg, ProviderCfg
from backend.llm.base import LLMError, LLMProvider
from backend.llm.jsonutil import extract_json
from backend.queue.errors import NeedsInput

T = TypeVar("T", bound=BaseModel)
MAX_RETRIES = 2
logger = logging.getLogger(__name__)


def _short(e: Exception, n: int = 800) -> str:
    s = str(e).strip()
    return s if len(s) <= n else s[:n] + "…"


def parse_output(text: str, schema: type[T], check: Callable[[T], None] | None = None) -> T:
    obj = schema.model_validate(extract_json(text))
    if check is not None:
        check(obj)
    return obj


class LLMGateway:
    def __init__(self, cfg: LLMCfg, providers: dict[str, LLMProvider], *,
                 gpu_guard: Callable[[], AbstractContextManager] | None = None,
                 run: Callable[..., Any] = subprocess.run,
                 override: Callable[[str], str | None] | None = None):
        self.cfg = cfg
        self.providers = providers
        self.gpu_guard = gpu_guard
        self._run = run
        self.override = override

    def provider_cfg(self, task: str, step: str | None = None,
                     emit: Callable[[str], None] | None = None) -> ProviderCfg:
        """这一次调用用哪个 provider：设置页的覆盖（按步骤 → 全局默认）→ config.yaml。
        设置里的 provider 失效时退回 config.yaml，并在任务日志里写一行。"""
        say = emit or (lambda m: None)
        pid = None
        if self.override is not None:
            try:
                pid = self.override(step or task)
            except Exception as e:  # 读设置失败不能挡住任务，退回 config
                logger.warning("读文本模型设置失败，按 config.yaml：%s", e)
                say(f"读设置页的文本模型失败（{e}），按 config.yaml")
        if pid:
            try:
                return self.cfg.provider(pid)
            except KeyError:
                fb = self.cfg.provider_for(task)
                msg = (f"设置页里的文本模型 {pid} 已失效（config.yaml 的 llm.providers 里没有），"
                       f"这次用 {fb.id}；到设置页改一下")
                logger.warning(msg)
                say(msg)
                return fb
        return self.cfg.provider_for(task)

    def generate(self, task: str, system: str, user: str, schema: type[T], *,
                 check: Callable[[T], None] | None = None,
                 log: Callable[[str], None] | None = None,
                 manual: dict | None = None, key: str | None = None,
                 step: str | None = None) -> T:
        emit = log or (lambda m: None)
        key = key or task
        pcfg = self.provider_cfg(task, step, emit)
        if pcfg.kind == "manual":
            return self._manual(task, key, system, user, schema, check, manual or {})
        provider = self.providers.get(pcfg.id)
        if provider is None:
            raise LLMError(f"LLM provider {pcfg.id} 没有初始化")
        guard = self.gpu_guard() if (pcfg.local_gpu and self.gpu_guard) else nullcontext()
        with guard:
            try:
                return self._loop(provider, pcfg, system, user, schema, check, emit)
            finally:
                if pcfg.local_gpu and pcfg.unload_cmd:
                    self._unload(pcfg, emit)

    def _loop(self, provider: LLMProvider, pcfg: ProviderCfg, system: str, user: str,
              schema: type[T], check, emit) -> T:
        messages = [{"role": "user", "content": user}]
        last_err, last_raw = "", ""
        for attempt in range(1 + MAX_RETRIES):
            res = provider.complete(system, messages, json_mode=True)
            emit(f"LLM {pcfg.id}（{res.model or pcfg.model or '-'}）第 {attempt + 1} 次，"
                 f"{res.seconds:.1f}s，usage={res.usage}")
            try:
                return parse_output(res.text, schema, check)
            except (LLMError, ValueError) as e:
                last_err, last_raw = _short(e), res.text
                emit(f"输出不合格：{last_err}")
                messages = messages + [
                    {"role": "assistant", "content": res.text[:8000]},
                    {"role": "user", "content": f"上面的输出不符合要求：{last_err}\n"
                                                "请修正后只输出一个符合格式的 JSON 对象，不要任何其他文字。"}]
        raise LLMError(f"{1 + MAX_RETRIES} 次输出都不合格：{last_err}\n"
                       f"最后一次原始输出：\n{last_raw[:3000]}")

    def _manual(self, task: str, key: str, system: str, user: str, schema: type[T], check,
                manual: dict) -> T:
        request = {"key": key, "task": task, "system": system, "user": user,
                   "schema": schema.model_json_schema()}
        text = manual.get(key)
        if text is None:
            raise NeedsInput(request)
        try:
            return parse_output(text, schema, check)
        except (LLMError, ValueError) as e:
            raise NeedsInput({**request, "error": _short(e)}) from None

    def _unload(self, pcfg: ProviderCfg, emit) -> None:
        try:
            p = self._run(pcfg.unload_cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=60)
            emit(f"已卸载 {pcfg.id} 的模型（退出码 {p.returncode}）")
        except Exception as e:
            emit(f"卸载 {pcfg.id} 的模型失败：{e}")
