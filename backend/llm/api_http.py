"""OpenAI / Anthropic / Gemini 官方 API 的公共部分：建 httpx 客户端、请求头、把 HTTP 错误翻成中文。

明文 Key 只放在请求头里（OpenAI：Authorization: Bearer；Anthropic：x-api-key；Gemini：x-goog-api-key），
不放进 URL（URL 会进 httpx 的日志和错误信息）。所有错误信息都过一遍 scrub()，接口回显的 Key 换成掩码。
"""
from __future__ import annotations

import json

import httpx

from backend.domain.api_keys import SERVICES, Creds, scrub

ANTHROPIC_VERSION = "2023-06-01"


def auth_headers(creds: Creds) -> dict[str, str]:
    key = creds.require()
    if creds.service == "openai":
        return {"Authorization": f"Bearer {key}"}
    if creds.service == "anthropic":
        return {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION}
    return {"x-goog-api-key": key}


def client(creds: Creds, *, timeout: float = 300.0,
           transport: httpx.BaseTransport | None = None) -> httpx.Client:
    """读环境变量里的代理（HTTPS_PROXY）：要走代理才能访问这几家的用户设它就行。
    注入了 transport（测试）时不读，免得代理设置把假 transport 绕过去。"""
    return httpx.Client(base_url=creds.base_url, timeout=timeout, headers=auth_headers(creds),
                        transport=transport, follow_redirects=True, trust_env=transport is None)


def error_detail(r: httpx.Response) -> str:
    """响应体里的错误说明：{error: {message}}（OpenAI / Anthropic / Gemini 都是这个形状）。"""
    try:
        d = r.json()
    except (json.JSONDecodeError, ValueError):
        return (r.text or "").strip()[:300]
    if isinstance(d, dict):
        e = d.get("error")
        if isinstance(e, dict):
            msg = e.get("message") or e.get("status") or e.get("type") or ""
            code = e.get("code") or e.get("type") or ""
            return f"{msg}{f'（{code}）' if code and str(code) not in str(msg) else ''}"[:400]
        if isinstance(e, str):
            return e[:400]
        if d.get("message"):
            return str(d["message"])[:400]
    return (r.text or "").strip()[:300]


def http_error_text(service_id: str, r: httpx.Response, creds: Creds | None = None,
                    model: str | None = None) -> str:
    vendor = SERVICES[service_id].vendor
    code = r.status_code
    detail = scrub(error_detail(r), creds.api_key if creds else None)
    tail = f"：{detail}" if detail else ""
    if code in (401, 403):
        why = (f"{vendor} 拒绝了 API Key（HTTP {code}）：Key 不对、过期，或者没有这个模型的权限。"
               "到「设置 → API 服务」检查")
    elif code == 404:
        why = (f"{vendor} 找不到模型或接口（HTTP 404）：{f'模型名 {model} ' if model else '模型名'}可能写错了，"
               "或 Base URL 不对")
    elif code == 429:
        why = f"{vendor} 限流或额度用完（HTTP 429）：稍后再试，或检查账户余额 / 配额"
    elif code >= 500:
        why = f"{vendor} 服务端出错（HTTP {code}），稍后再试"
    else:
        why = f"{vendor} 不接受这个请求（HTTP {code}）"
    return why + tail


def transport_error_text(service_id: str, creds: Creds, e: Exception) -> str:
    vendor = SERVICES[service_id].vendor
    return scrub(f"连不上 {vendor}（{creds.base_url}）：{type(e).__name__} {e}"
                 "。检查网络 / 代理（HTTPS_PROXY）或 Base URL", creds.api_key)
