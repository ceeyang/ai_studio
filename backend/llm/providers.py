"""LLM provider：OpenAI 兼容接口（LM Studio / DeepSeek / Agnes…）、本机 Claude Code。"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

import httpx

from backend.config import LLMCfg, ProviderCfg
from backend.llm.base import LLMError, LLMProvider, LLMResult, VisionRejected, image_b64

logger = logging.getLogger(__name__)


def flatten_messages(messages: list[dict]) -> str:
    if len(messages) == 1:
        return messages[0]["content"]
    label = {"user": "【用户】", "assistant": "【助手】"}
    return "\n\n".join(f"{label.get(m['role'], m['role'])}\n{m['content']}" for m in messages)


def _with_images_openai(messages: list[dict], images) -> list[dict]:
    """OpenAI 兼容接口的看图写法：第一条用户消息的 content 换成 [文字, image_url(data URI)…]。"""
    if not images:
        return messages
    parts = [{"type": "text", "text": messages[0]["content"]}]
    for p in images:
        mt, data = image_b64(p)
        parts.append({"type": "image_url", "image_url": {"url": f"data:{mt};base64,{data}"}})
    return [{**messages[0], "content": parts}, *messages[1:]]


def claude_image_blocks(images) -> list[dict]:
    """Claude 的图片内容块（base64）。"""
    out = []
    for p in images or []:
        mt, data = image_b64(p)
        out.append({"type": "image", "source": {"type": "base64", "media_type": mt, "data": data}})
    return out


VISION_REJECT_STATUS = (400, 415, 422)


class OpenAIProvider:
    def __init__(self, cfg: ProviderCfg, *, transport: httpx.BaseTransport | None = None):
        if not cfg.base_url:
            raise ValueError(f"LLM provider {cfg.id} 缺少 base_url")
        self.id = cfg.id
        self.cfg = cfg
        headers = {}
        key = cfg.api_key()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        self._http = httpx.Client(base_url=cfg.base_url.rstrip("/"), timeout=cfg.timeout,
                                  trust_env=False, headers=headers, transport=transport)
        # 能不能看图事先不知道（看模型）：照发 image_url，接口报错时提示换模型
        self.supports_images = cfg.vision is not False

    def complete(self, system: str, messages: list[dict], *, json_mode: bool = True,
                 images=None) -> LLMResult:
        messages = _with_images_openai(messages, images)
        body: dict = {"messages": [{"role": "system", "content": system}, *messages],
                      "temperature": 0.7, "stream": False}
        if self.cfg.model:
            body["model"] = self.cfg.model
        t0 = time.monotonic()
        last: Exception | None = None
        for attempt in range(3):
            try:
                r = self._http.post("/chat/completions", json=body)
                break
            except httpx.TransportError as e:  # LM Studio 加载模型时会重置连接
                last = e
                time.sleep(2)
        else:
            raise LLMError(f"连不上 {self.id}（{self.cfg.base_url}）：{last}")
        if r.status_code >= 400:
            # 只有「请求本身不被接受」才可能是不支持图片；401 / 429 / 5xx 是鉴权、限流、服务端的问题，照原样报
            if images and r.status_code in VISION_REJECT_STATUS:
                raise VisionRejected(f"带图片的请求被拒：HTTP {r.status_code} {r.text[:300]}")
            raise LLMError(f"{self.id} 返回 HTTP {r.status_code}：{r.text[:500]}")
        d = r.json()
        text = d["choices"][0]["message"].get("content") or ""
        return LLMResult(text=text, model=d.get("model"), usage=d.get("usage"),
                         seconds=time.monotonic() - t0)


class ClaudeProvider:
    """走本机 Claude Code 订阅，不需要 API key。"""

    def __init__(self, cfg: ProviderCfg, *, run: Callable = subprocess.run,
                 which: Callable[[str], str | None] = shutil.which):
        self.id = cfg.id
        self.cfg = cfg
        self._run = run
        self._which = which
        # 看图：SDK 走流式输入发图片内容块，claude -p 走 --input-format stream-json
        self.supports_images = cfg.vision is not False

    def complete(self, system: str, messages: list[dict], *, json_mode: bool = True,
                 images=None) -> LLMResult:
        prompt = flatten_messages(messages)
        t0 = time.monotonic()
        if self.cfg.use_sdk:
            try:
                coro = self._sdk_images(system, prompt, images) if images else self._sdk(system, prompt)
                text = asyncio.run(coro)
                return LLMResult(text=text, model=self.cfg.model, seconds=time.monotonic() - t0)
            except Exception as e:
                logger.warning("Claude Agent SDK 调用失败，退回 claude -p：%s", e)
        if images:
            return self._cli_images(system, prompt, images, t0)
        return self._cli(system, prompt, t0)

    @staticmethod
    def user_message(prompt: str, images) -> dict:
        """流式输入的一条用户消息：图片内容块在前、文字在后。"""
        return {"type": "user", "parent_tool_use_id": None, "session_id": "default",
                "message": {"role": "user",
                            "content": [*claude_image_blocks(images),
                                        {"type": "text", "text": prompt}]}}

    async def _sdk(self, system: str, prompt) -> str:
        """prompt 是字符串，或（看图时）流式输入的消息迭代器。"""
        from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

        opts = ClaudeAgentOptions(system_prompt=system, max_turns=1, allowed_tools=[],
                                  model=self.cfg.model, cwd=tempfile.gettempdir())
        result = None
        async for msg in query(prompt=prompt, options=opts):
            if isinstance(msg, ResultMessage):
                if msg.is_error:
                    raise LLMError(f"Claude Agent SDK 报错：{msg.result}")
                result = msg.result
        if not result:
            raise LLMError("Claude Agent SDK 没有返回结果")
        return result

    async def _sdk_images(self, system: str, prompt: str, images) -> str:
        msg = self.user_message(prompt, images)

        async def stream():
            yield msg

        return await self._sdk(system, stream())

    def _cli_images(self, system: str, prompt: str, images, t0: float) -> LLMResult:
        """claude -p 看图：--input-format stream-json，stdin 发一条带图片内容块的用户消息，
        输出也是一行一个 JSON（stream-json），取最后的 result。"""
        exe = self._which("claude")
        if not exe:
            raise LLMError("找不到 claude 命令（Claude Code CLI），装好后再试，或换个 provider")
        line = json.dumps(self.user_message(prompt, images), ensure_ascii=False)
        with tempfile.TemporaryDirectory() as tmp:
            sp = Path(tmp) / "system.md"
            sp.write_text(system, encoding="utf-8")
            cmd = [exe, "-p", "--input-format", "stream-json", "--output-format", "stream-json",
                   "--verbose", "--max-turns", "1", "--system-prompt-file", str(sp)]
            if self.cfg.model:
                cmd += ["--model", self.cfg.model]
            if exe.lower().endswith((".cmd", ".bat")):
                cmd = ["cmd", "/c", *cmd]
            p = self._run(cmd, input=line + "\n", capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=self.cfg.timeout, cwd=tmp)
        if p.returncode != 0 and not (p.stdout or "").strip():
            raise LLMError(f"claude -p 失败（退出码 {p.returncode}）：{(p.stderr or '')[-800:]}")
        d = None
        for raw in (p.stdout or "").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and obj.get("type") == "result":
                d = obj
        if d is None:
            raise LLMError(f"claude -p 没有返回结果：{(p.stdout or p.stderr or '')[:500]}")
        if d.get("is_error") or d.get("subtype") not in (None, "success"):
            raise LLMError(f"claude -p 报错：{d.get('result') or d.get('subtype')}")
        return LLMResult(text=d.get("result") or "", model=self.cfg.model, usage=d.get("usage"),
                         seconds=time.monotonic() - t0)

    def _cli(self, system: str, prompt: str, t0: float) -> LLMResult:
        exe = self._which("claude")
        if not exe:
            raise LLMError("找不到 claude 命令（Claude Code CLI），装好后再试，或换个 provider")
        with tempfile.TemporaryDirectory() as tmp:
            sp = Path(tmp) / "system.md"
            sp.write_text(system, encoding="utf-8")
            cmd = [exe, "-p", "--output-format", "json", "--max-turns", "1",
                   "--system-prompt-file", str(sp)]
            if self.cfg.model:
                cmd += ["--model", self.cfg.model]
            if exe.lower().endswith((".cmd", ".bat")):
                cmd = ["cmd", "/c", *cmd]
            p = self._run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=self.cfg.timeout, cwd=tmp)
        if p.returncode != 0 and not (p.stdout or "").strip():
            raise LLMError(f"claude -p 失败（退出码 {p.returncode}）：{(p.stderr or '')[-800:]}")
        try:
            d = json.loads(p.stdout)
        except json.JSONDecodeError:
            raise LLMError(f"claude -p 输出不是 JSON：{(p.stdout or '')[:500]}") from None
        if isinstance(d, list):
            d = next((x for x in reversed(d) if isinstance(x, dict) and x.get("type") == "result"), {})
        if d.get("is_error") or d.get("subtype") not in (None, "success"):
            raise LLMError(f"claude -p 报错：{d.get('result') or d.get('subtype')}")
        return LLMResult(text=d.get("result") or "", model=self.cfg.model, usage=d.get("usage"),
                         seconds=time.monotonic() - t0)


def build_providers(cfg: LLMCfg, *, creds=None) -> dict[str, LLMProvider]:
    """creds：API 服务的 Key 解析（ApiKeyStore.resolve），不给只看环境变量。"""
    from backend.llm.api_providers import PROVIDER_CLASSES

    out: dict[str, LLMProvider] = {}
    for p in cfg.providers:
        if p.kind == "openai":
            out[p.id] = OpenAIProvider(p)
        elif p.kind == "claude":
            out[p.id] = ClaudeProvider(p)
        elif p.kind in PROVIDER_CLASSES:
            out[p.id] = PROVIDER_CLASSES[p.kind](p, creds=creds)
    return out
