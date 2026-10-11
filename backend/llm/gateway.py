"""LLM 网关：按任务路由 provider，解析并校验 JSON，不合格就带着错误重试。

本机 GPU 上的 provider（LM Studio）在 gpu_guard 里调用：先等 GPU 任务让出显卡，
调用结束后卸载模型，再把显卡还给 ComfyUI。

用哪个 provider：每次调用时先问 override(step)（设置页的「按步骤单独设置 → 默认文本模型」，
见 backend/domain/settings.py），没有再按 config.yaml 的 llm.routes / llm.default。改设置不用重启。
"""
from __future__ import annotations

import inspect
import logging
import subprocess
import time
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from backend.config import LLMCfg, ProviderCfg
from backend.llm.base import LLMError, LLMProvider, VisionRejected
from backend.llm.jsonutil import extract_json
from backend.queue.errors import NeedsInput
from backend.queue.trace import trace_add, trace_rel, trace_update

T = TypeVar("T", bound=BaseModel)
MAX_RETRIES = 2
logger = logging.getLogger(__name__)


def _short(e: Exception, n: int = 800) -> str:
    s = str(e).strip()
    return s if len(s) <= n else s[:n] + "…"


def _wants_last(check) -> bool:
    try:
        return "last" in inspect.signature(check).parameters
    except (TypeError, ValueError):
        return False


def step_label(step: str) -> str:
    try:
        from backend.domain.settings import STEP_LABEL
    except Exception:  # pragma: no cover - 只为了出错信息好看
        return step
    return STEP_LABEL.get(step, step)


def no_vision_error(pid: str, step: str, detail: str = "") -> LLMError:
    """provider 看不了图时的提示：去设置页把这一步的文本模型换成能看图的。"""
    why = f"（{detail}）" if detail else ""
    return LLMError(f"文本模型 {pid} 看不了图片{why}。到「设置 → 按步骤单独设置」把「{step_label(step)}」"
                    "这一步的文本模型换成能看图的（如本机 Claude，或支持视觉的 OpenAI 兼容模型），"
                    "或换成「手动粘贴」自己去网页上问")


def _dump(obj: Any) -> Any:
    try:
        return obj.model_dump(mode="json")
    except Exception:
        return str(obj)


def _trace_title(task: str, step: str | None, key: str | None) -> str:
    """调用明细的标题：步骤名（script 的两次调用再带上 key：script_structure / script_visual）。"""
    name = step or task
    if key and key not in (task, step) and ":" not in key:
        name = f"{name} · {key}"
    return name


def parse_output(text: str, schema: type[T], check: Callable[..., None] | None = None, *,
                 last: bool = True) -> T:
    """解析并校验。check 可以声明关键字参数 last：这是不是最后一次机会（之后不会再让模型重写），
    最后一次可以放宽能自动兜底的规则（比如按比例缩放时长），免得整段失败。"""
    obj = schema.model_validate(extract_json(text))
    if check is not None:
        if _wants_last(check):
            check(obj, last=last)
        else:
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
                 step: str | None = None, images: Sequence[Path | str] | None = None,
                 image_urls: Sequence[str] | None = None) -> T:
        """images：要让模型看的本机图片（附在第一条用户消息上）；image_urls 只给手动粘贴的页面显示用。"""
        emit = log or (lambda m: None)
        key = key or task
        images = [Path(p) for p in (images or [])]
        pcfg = self.provider_cfg(task, step, emit)
        info = {"task": task, "step": step or task, "key": key,
                "title": _trace_title(task, step, key)}
        if pcfg.kind == "manual":
            return self._manual(task, key, system, user, schema, check, manual or {},
                                images, list(image_urls or []), info=info)
        provider = self.providers.get(pcfg.id)
        if provider is None:
            raise LLMError(f"LLM provider {pcfg.id} 没有初始化")
        if images and (pcfg.vision is False or not getattr(provider, "supports_images", False)):
            raise no_vision_error(pcfg.id, step or task)
        guard = self.gpu_guard() if (pcfg.local_gpu and self.gpu_guard) else nullcontext()
        with guard:
            try:
                return self._loop(provider, pcfg, system, user, schema, check, emit, images,
                                  info=info)
            except VisionRejected as e:
                raise no_vision_error(pcfg.id, step or task, str(e)) from None
            finally:
                if pcfg.local_gpu and pcfg.unload_cmd:
                    self._unload(pcfg, emit)

    def _loop(self, provider: LLMProvider, pcfg: ProviderCfg, system: str, user: str,
              schema: type[T], check, emit, images: list[Path] | None = None, *,
              info: dict | None = None) -> T:
        info = info or {}
        messages = [{"role": "user", "content": user}]
        last_err, last_raw = "", ""
        for attempt in range(1 + MAX_RETRIES):
            kw: dict[str, Any] = {"images": images} if images else {}
            if getattr(provider, "accepts_log", False):   # 网页文本模型：等回复时往任务日志写进度
                kw["log"] = emit
            # 调用明细：发请求前先记下发了什么（任务跑着时就能看到），出结果后补回复和解析结果
            seq = trace_add("llm", info.get("title") or pcfg.id, {
                "task": info.get("task"), "step": info.get("step"), "key": info.get("key"),
                "provider": pcfg.id, "provider_kind": pcfg.kind, "model": pcfg.model or None,
                "attempt": attempt + 1, "max_attempts": 1 + MAX_RETRIES, "status": "running",
                "system": system, "user": user,
                # 第 2 次起：上一次的回复和「哪里不合格、请修正」也一起发给了模型
                "followups": messages[1:],
                "images": [trace_rel(p) for p in images or []],
                "schema": getattr(schema, "__name__", str(schema))})
            t0 = time.monotonic()
            try:
                res = provider.complete(system, messages, json_mode=True, **kw)
            except BaseException as e:
                trace_update(seq, {"status": "error", "error": _short(e, 3000),
                                   "seconds": round(time.monotonic() - t0, 2)})
                raise
            emit(f"LLM {pcfg.id}（{res.model or pcfg.model or '-'}）第 {attempt + 1} 次，"
                 f"{res.seconds:.1f}s，usage={res.usage}")
            done = {"model": res.model or pcfg.model or None, "reply": res.text, "usage": res.usage,
                    "seconds": round(res.seconds or (time.monotonic() - t0), 2)}
            try:
                # last 按真实的调用次数算（输出连 schema 都不合格时 check 根本没被调用）
                obj = parse_output(res.text, schema, check, last=attempt == MAX_RETRIES)
            except (LLMError, ValueError) as e:
                last_err, last_raw = _short(e), res.text
                trace_update(seq, {**done, "status": "invalid", "error": _short(e, 3000)})
                emit(f"输出不合格：{last_err}")
                messages = messages + [
                    {"role": "assistant", "content": res.text[:8000]},
                    {"role": "user", "content": f"上面的输出不符合要求：{last_err}\n"
                                                "请修正后只输出一个符合格式的 JSON 对象，不要任何其他文字。"}]
                continue
            trace_update(seq, {**done, "status": "ok", "parsed": _dump(obj)})
            return obj
        raise LLMError(f"{1 + MAX_RETRIES} 次输出都不合格：{last_err}\n"
                       f"最后一次原始输出：\n{last_raw[:3000]}")

    def _manual(self, task: str, key: str, system: str, user: str, schema: type[T], check,
                manual: dict, images: list[Path] | None = None,
                image_urls: list[str] | None = None, *, info: dict | None = None) -> T:
        request = {"key": key, "task": task, "system": system, "user": user,
                   "schema": schema.model_json_schema()}
        if images:
            # 手动粘贴：把图片的本机路径（和能在页面上打开的地址）给人看，让人连图一起发给模型
            request["images"] = [str(p) for p in images]
            if image_urls:
                request["image_urls"] = list(image_urls)
        text = manual.get(key)
        info = info or {}
        title = info.get("title") or task
        base = {"task": task, "step": info.get("step") or task, "key": key, "provider": "manual",
                "provider_kind": "manual", "model": None, "attempt": 1, "max_attempts": 1,
                "system": system, "user": user, "images": [trace_rel(p) for p in images or []],
                "schema": getattr(schema, "__name__", str(schema))}
        if text is None:
            trace_add("llm", title, {**base, "status": "waiting_input",
                                     "note": "等人工把模型输出粘贴进来"})
            raise NeedsInput(request)
        seq = trace_add("llm", title, {**base, "status": "running", "reply": text})
        try:
            # 手工粘贴的输出没有「让模型再写一次」的循环：能自动兜底的规则直接兜底
            obj = parse_output(text, schema, check, last=True)
        except (LLMError, ValueError) as e:
            trace_update(seq, {"status": "invalid", "error": _short(e, 3000)})
            raise NeedsInput({**request, "error": _short(e)}) from None
        trace_update(seq, {"status": "ok", "parsed": _dump(obj)})
        return obj

    def _unload(self, pcfg: ProviderCfg, emit) -> None:
        try:
            p = self._run(pcfg.unload_cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=60)
            emit(f"已卸载 {pcfg.id} 的模型（退出码 {p.returncode}）")
        except Exception as e:
            emit(f"卸载 {pcfg.id} 的模型失败：{e}")
