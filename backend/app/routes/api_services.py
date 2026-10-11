"""设置页「API 服务」：OpenAI（GPT）、Anthropic（Claude）、Google（Gemini）的 Key、Base URL、模型名。

- GET  /api/api-services                 每家一行：是否已配置、来源（设置页 / 环境变量）、掩码、Base URL、模型名
- PUT  /api/api-services/{id}            {api_key?, clear_key?, base_url?, models?}：
                                         api_key 空 / 不传 = 不改；clear_key=true 删掉设置页存的 Key；
                                         base_url 空串 = 恢复默认；models 里空值 = 恢复默认
- POST /api/api-services/{id}/test       调这家最便宜的「列模型」接口，返回 {ok, message}

响应里绝不出现明文 Key（只有 sk-…abcd 这样的掩码）。请求体自己校验（不用 pydantic 模型）：
FastAPI 的 422 会把收到的字段值原样回显，Key 填错字段名时就会出现在响应里。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from backend.app.deps import services
from backend.domain.api_keys import MODEL_KINDS, SERVICES, ApiKeyStore
from backend.llm.api_providers import check_connection
from backend.queue.errors import JobError

router = APIRouter(prefix="/api", tags=["api-services"])
FIELDS = {"api_key", "clear_key", "base_url", "models"}


def _store(request: Request) -> ApiKeyStore:
    sv = services(request)
    store = getattr(sv, "api_keys", None)
    if store is None:
        raise JobError("API 服务的设置没有初始化")
    return store


def _service(sid: str) -> str:
    if sid not in SERVICES:
        raise LookupError(f"没有这个 API 服务：{sid}，可选：{', '.join(SERVICES)}")
    return sid


@router.get("/api-services")
def list_services(request: Request):
    return {"services": _store(request).all_public()}


@router.put("/api-services/{sid}")
async def update_service(sid: str, request: Request):
    _service(sid)
    try:
        body: Any = await request.json()
    except Exception:  # noqa: BLE001
        raise JobError("请求体要是 JSON 对象") from None
    if not isinstance(body, dict):
        raise JobError("请求体要是 JSON 对象")
    extra = sorted(set(body) - FIELDS)
    if extra:   # 只报字段名，不回显值
        raise JobError(f"不认识的字段：{', '.join(extra)}，可用：{', '.join(sorted(FIELDS))}")
    key = body.get("api_key")
    if key is not None and not isinstance(key, str):
        raise JobError("api_key 要是字符串")
    if isinstance(key, str) and key.strip() and (len(key.strip()) < 8 or any(ch.isspace() for ch in key.strip())):
        raise JobError("API Key 看起来不对（太短或中间有空格），检查一下有没有复制全")
    base = body.get("base_url")
    if base is not None:
        if not isinstance(base, str):
            raise JobError("base_url 要是字符串")
        if base.strip() and not base.strip().lower().startswith(("http://", "https://")):
            raise JobError("Base URL 要以 http:// 或 https:// 开头")
    models = body.get("models")
    if models is not None:
        allowed = set(SERVICES[sid].models)
        if not isinstance(models, dict) or not all(isinstance(v, (str, type(None))) for v in models.values()):
            raise JobError("models 要是 {text / image / video: 模型名} 这样的对象")
        bad = sorted(set(models) - allowed)
        if bad:
            raise JobError(f"{SERVICES[sid].label} 没有这些模型类型：{', '.join(bad)}，"
                           f"可选：{', '.join(k for k in MODEL_KINDS if k in allowed)}")
    store = _store(request)
    store.update(sid, api_key=key or None, clear_key=bool(body.get("clear_key")),
                 base_url=base, models=models)
    return store.public(sid)


@router.post("/api-services/{sid}/test")
def test_service(sid: str, request: Request):
    _service(sid)
    store = _store(request)
    res = check_connection(sid, store.resolve(sid))
    store.last_test[sid] = res
    return {**res, "service": store.public(sid)}
