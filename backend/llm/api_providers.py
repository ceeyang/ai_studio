"""文本模型：OpenAI / Anthropic / Gemini 官方 API（Key 在设置页「API 服务」里填，或用环境变量）。

接口：
- OpenAI：POST {base}/chat/completions（base 默认 https://api.openai.com/v1）。看图：content 里放
  image_url（data URI）。json_mode：提示词里写了 JSON 时加 response_format={"type": "json_object"}
  （OpenAI 要求消息里出现 "json" 字样才允许这个参数）。
- Anthropic：POST {base}/v1/messages，anthropic-version: 2023-06-01。看图：image 内容块（base64）。
  没有 JSON 模式参数，靠提示词。
- Gemini：POST {base}/v1beta/models/{model}:generateContent。看图：inlineData。json_mode：
  generationConfig.responseMimeType = application/json。

Key、Base URL、模型名每次调用时现读（creds 回调），改了不用重启。Key 只放请求头。
"""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import httpx

from backend.config import ProviderCfg
from backend.domain.api_keys import Creds, MissingKey, env_creds, scrub
from backend.llm.api_http import client, http_error_text, transport_error_text
from backend.llm.base import LLMError, LLMResult, VisionRejected, image_b64

# 文本模型 provider id → API 服务（见 backend/domain/api_keys.py 的 SERVICES）
API_PROVIDERS: dict[str, tuple[str, str]] = {
    # id: (kind, service)
    "openai_api": ("openai_api", "openai"),
    "anthropic_api": ("anthropic", "anthropic"),
    "gemini_api": ("gemini", "gemini"),
}
KIND_SERVICE = {kind: svc for kind, svc in API_PROVIDERS.values()}
API_KINDS = tuple(KIND_SERVICE)
API_LABEL = {"openai_api": "OpenAI GPT", "anthropic": "Anthropic Claude", "gemini": "Google Gemini"}
ANTHROPIC_MAX_TOKENS = 16000
VISION_HINTS = ("image", "vision", "图片", "multimodal")

CredsFn = Callable[[str], Creds]


def ensure_api_providers(llm_cfg) -> None:
    """config.yaml 里没写的话，把三个 API provider 加进 llm.providers（设置页下拉、按步骤设置都能选）。"""
    have = {p.id for p in llm_cfg.providers}
    have_kinds = {p.kind for p in llm_cfg.providers}
    for pid, (kind, _svc) in API_PROVIDERS.items():
        if pid in have or kind in have_kinds:
            continue
        llm_cfg.providers.append(ProviderCfg(id=pid, kind=kind, timeout=600.0))


class _ApiProvider:
    service = ""

    def __init__(self, cfg: ProviderCfg, *, creds: CredsFn | None = None,
                 transport: httpx.BaseTransport | None = None):
        self.id = cfg.id
        self.cfg = cfg
        self._creds = creds or env_creds
        self._transport = transport
        self.supports_images = cfg.vision is not False

    # ------------------------------------------------------------ 公共
    def creds(self) -> Creds:
        try:
            c = self._creds(self.service)
            c.require()
        except MissingKey as e:
            raise LLMError(str(e)) from None
        return c

    def model(self, c: Creds) -> str:
        """设置页「API 服务」里填的模型名 → config.yaml 里 provider 的 model → 默认常量。"""
        return c.model("text", self.cfg.model)

    def _post(self, c: Creds, path: str, body: dict, model: str, images) -> dict:
        last: Exception | None = None
        with client(c, timeout=self.cfg.timeout, transport=self._transport) as http:
            for _ in range(2):
                try:
                    r = http.post(path, json=body)
                    break
                except httpx.TransportError as e:
                    last = e
                    time.sleep(1)
            else:
                raise LLMError(transport_error_text(self.service, c, last))
        if r.status_code >= 400:
            msg = http_error_text(self.service, r, c, model)
            if images and r.status_code in (400, 415, 422) and any(h in msg.lower() for h in VISION_HINTS):
                raise VisionRejected(msg)
            raise LLMError(msg)
        try:
            return r.json()
        except ValueError:
            raise LLMError(scrub(f"{self.id} 返回的不是 JSON：{r.text[:300]}", c.api_key)) from None


class OpenAIApiProvider(_ApiProvider):
    service = "openai"

    def complete(self, system: str, messages: list[dict], *, json_mode: bool = True,
                 images=None) -> LLMResult:
        c = self.creds()
        model = self.model(c)
        msgs: list[dict] = [{"role": "system", "content": system}, *messages]
        if images:
            parts: list[dict[str, Any]] = [{"type": "text", "text": messages[0]["content"]}]
            for p in images:
                mt, data = image_b64(p)
                parts.append({"type": "image_url", "image_url": {"url": f"data:{mt};base64,{data}"}})
            msgs[1] = {**messages[0], "content": parts}
        body: dict[str, Any] = {"model": model, "messages": msgs}
        text_all = system + " ".join(str(m.get("content")) for m in messages)
        if json_mode and "json" in text_all.lower():
            body["response_format"] = {"type": "json_object"}
        t0 = time.monotonic()
        d = self._post(c, "/chat/completions", body, model, images)
        try:
            text = d["choices"][0]["message"].get("content") or ""
        except (KeyError, IndexError, TypeError):
            raise LLMError(f"OpenAI 返回里没有 choices：{str(d)[:300]}") from None
        return LLMResult(text=text, model=d.get("model") or model, usage=d.get("usage"),
                         seconds=time.monotonic() - t0)


class AnthropicProvider(_ApiProvider):
    service = "anthropic"

    def complete(self, system: str, messages: list[dict], *, json_mode: bool = True,
                 images=None) -> LLMResult:
        c = self.creds()
        model = self.model(c)
        msgs = []
        for i, m in enumerate(messages):
            content: Any = m["content"]
            if i == 0 and images:
                blocks = []
                for p in images:
                    mt, data = image_b64(p)
                    blocks.append({"type": "image", "source": {"type": "base64", "media_type": mt,
                                                               "data": data}})
                content = [*blocks, {"type": "text", "text": m["content"]}]
            msgs.append({"role": m["role"], "content": content})
        body = {"model": model, "max_tokens": ANTHROPIC_MAX_TOKENS, "system": system, "messages": msgs}
        t0 = time.monotonic()
        d = self._post(c, "/v1/messages", body, model, images)
        text = "".join(b.get("text", "") for b in d.get("content") or []
                       if isinstance(b, dict) and b.get("type") == "text")
        if not text and d.get("stop_reason") == "refusal":
            raise LLMError("Claude 拒绝回答这个请求（stop_reason=refusal）")
        return LLMResult(text=text, model=d.get("model") or model, usage=d.get("usage"),
                         seconds=time.monotonic() - t0)


class GeminiProvider(_ApiProvider):
    service = "gemini"

    def complete(self, system: str, messages: list[dict], *, json_mode: bool = True,
                 images=None) -> LLMResult:
        c = self.creds()
        model = self.model(c)
        contents = []
        for i, m in enumerate(messages):
            parts: list[dict] = []
            if i == 0 and images:
                for p in images:
                    mt, data = image_b64(p)
                    parts.append({"inlineData": {"mimeType": mt, "data": data}})
            parts.append({"text": m["content"]})
            contents.append({"role": "model" if m["role"] == "assistant" else "user", "parts": parts})
        body: dict[str, Any] = {"systemInstruction": {"parts": [{"text": system}]}, "contents": contents}
        if json_mode:
            body["generationConfig"] = {"responseMimeType": "application/json"}
        t0 = time.monotonic()
        d = self._post(c, f"/v1beta/models/{model}:generateContent", body, model, images)
        text = gemini_text(d)
        return LLMResult(text=text, model=d.get("modelVersion") or model, usage=d.get("usageMetadata"),
                         seconds=time.monotonic() - t0)


def gemini_text(d: dict) -> str:
    cands = d.get("candidates") or []
    if not cands:
        block = (d.get("promptFeedback") or {}).get("blockReason")
        raise LLMError(f"Gemini 没有返回内容{f'（被拦下：{block}）' if block else ''}")
    cand = cands[0]
    parts = (cand.get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if isinstance(p, dict) and not p.get("thought"))
    if not text and cand.get("finishReason") not in (None, "STOP", "MAX_TOKENS"):
        raise LLMError(f"Gemini 没有生成文字（finishReason={cand.get('finishReason')}）")
    return text


PROVIDER_CLASSES = {"openai_api": OpenAIApiProvider, "anthropic": AnthropicProvider, "gemini": GeminiProvider}


# ---------------------------------------------------------------- 测试连接
def check_connection(service_id: str, creds: Creds, *,
                    transport: httpx.BaseTransport | None = None) -> dict:
    """调各家最便宜的「列模型」接口（不生成内容、不扣费），返回 {ok, message}（中文，不含 Key）。"""
    if not creds.configured:
        from backend.domain.api_keys import no_key_hint
        return {"ok": False, "message": no_key_hint(service_id)}
    if service_id in TASK_LIST:
        return _check_task_list(service_id, creds, transport)
    path, params = {"openai": ("/models", None), "anthropic": ("/v1/models", {"limit": 100}),
                    "gemini": ("/v1beta/models", {"pageSize": 1000})}[service_id]
    try:
        with client(creds, timeout=20.0, transport=transport) as http:
            r = http.get(path, params=params)
    except httpx.HTTPError as e:
        return {"ok": False, "message": transport_error_text(service_id, creds, e)}
    if r.status_code >= 400:
        return {"ok": False, "message": http_error_text(service_id, r, creds)}
    try:
        d = r.json()
    except ValueError:
        return {"ok": False, "message": "返回的不是 JSON：Base URL 可能指到了网页而不是 API"}
    rows = d.get("data") if service_id != "gemini" else d.get("models")
    rows = rows if isinstance(rows, list) else []
    ids = {str(x.get("id") or x.get("name") or "").removeprefix("models/") for x in rows if isinstance(x, dict)}
    used = [m for m in creds.models.values() if m]
    missing = [m for m in used if ids and m not in ids]
    msg = f"连接成功，能看到 {len(ids)} 个模型"
    if missing:
        msg += f"；列表里没看到 {'、'.join(missing)}（模型名可能写错，或这个 Key 没有权限）"
    return {"ok": True, "message": msg}


# 火山方舟、可灵没有「列模型」接口（方舟的模型列表在管控面，要另一套 AK/SK）：
# 改调「查询视频生成任务列表」，只读、不生成内容、不扣费，能验证 Key / 签名和区域地址。
TASK_LIST = {"ark": ("/contents/generations/tasks", {"page_num": 1, "page_size": 1}),
             "kling": ("/v1/videos/image2video", {"pageNum": 1, "pageSize": 1})}


def _check_task_list(service_id: str, creds: Creds, transport) -> dict:
    path, params = TASK_LIST[service_id]
    try:
        with client(creds, timeout=20.0, transport=transport) as http:
            r = http.get(path, params=params)
    except httpx.HTTPError as e:
        return {"ok": False, "message": transport_error_text(service_id, creds, e)}
    if r.status_code >= 400:
        return {"ok": False, "message": http_error_text(service_id, r, creds)}
    try:
        d = r.json()
    except ValueError:
        return {"ok": False, "message": "返回的不是 JSON：Base URL 可能指到了网页而不是 API"}
    if service_id == "kling" and isinstance(d, dict) and d.get("code") not in (None, 0):
        return {"ok": False, "message": http_error_text(service_id, r, creds)}
    how = "AccessKey / SecretKey 签名" if creds.aksk else "API Key"
    return {"ok": True, "message": f"连接成功（{how} 有效，能查询任务列表）· {creds.base_url}"}
