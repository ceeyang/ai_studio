"""设置页「API 服务」：OpenAI（GPT）、Anthropic（Claude）、Google（Gemini）、火山方舟（Seedance）、
可灵的 Key、Base URL、区域、模型名。

- GET  /api/api-services                 每家一行：是否已配置、来源（设置页 / 环境变量）、掩码、Base URL、区域、模型名
- PUT  /api/api-services/{id}            {api_key?, clear_key?, base_url?, models?, region?, access_key?, secret_key?}：
                                         api_key 空 / 不传 = 不改；clear_key=true 删掉设置页存的所有 Key（含 AK/SK）；
                                         base_url 空串 = 恢复默认；models 里空值 = 恢复默认；
                                         region（火山方舟、可灵）：cn / intl，空串 = 恢复默认（国内）；
                                         access_key / secret_key（只有可灵）：两个要一起有（这次一起填，或另一个已经存过）
- POST /api/api-services/{id}/test       调这家最便宜的只读接口（列模型 / 查任务列表），返回 {ok, message}

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
FIELDS = {"api_key", "clear_key", "base_url", "models", "region", "access_key", "secret_key"}


def _check_key(value: Any, field: str, what: str) -> None:
    if value is not None and not isinstance(value, str):
        raise JobError(f"{field} 要是字符串")
    if isinstance(value, str) and value.strip() and (len(value.strip()) < 8 or any(ch.isspace() for ch in value.strip())):
        raise JobError(f"{what} 看起来不对（太短或中间有空格），检查一下有没有复制全")


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
    svc = SERVICES[sid]
    key = body.get("api_key")
    _check_key(key, "api_key", "API Key")
    ak, sk = body.get("access_key"), body.get("secret_key")
    if (ak or sk) and not svc.env_aksk:
        raise JobError(f"{svc.label} 只用 API Key，没有 AccessKey / SecretKey")
    _check_key(ak, "access_key", "AccessKey")
    _check_key(sk, "secret_key", "SecretKey")
    region = body.get("region")
    if region is not None:
        if not svc.regions:
            raise JobError(f"{svc.label} 没有区域选项")
        if not isinstance(region, str) or (region.strip() and region.strip() not in svc.regions):
            raise JobError(f"区域只能是：{', '.join(svc.regions)}")
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
    if svc.env_aksk and (ak or sk):
        # AK 和 SK 要配对：这次只填了一个时，另一个得已经存过（清除时一起删了就不算）
        row = {} if body.get("clear_key") else store._row(sid)
        if not ((ak or row.get("access_key")) and (sk or row.get("secret_key"))):
            raise JobError("AccessKey 和 SecretKey 要一起填")
    store.update(sid, api_key=key or None, clear_key=bool(body.get("clear_key")),
                 base_url=base, models=models, region=region, access_key=ak or None, secret_key=sk or None)
    return store.public(sid)


@router.post("/api-services/{sid}/test")
def test_service(sid: str, request: Request):
    _service(sid)
    store = _store(request)
    res = check_connection(sid, store.resolve(sid))
    store.last_test[sid] = res
    return {**res, "service": store.public(sid)}
