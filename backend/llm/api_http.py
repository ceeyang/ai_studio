"""官方 API（OpenAI / Anthropic / Gemini / 火山方舟 / 可灵）的公共部分：建 httpx 客户端、请求头、
可灵的 JWT 签名、把 HTTP 错误翻成中文。

明文 Key 只放在请求头里（OpenAI、火山方舟、可灵：Authorization: Bearer；Anthropic：x-api-key；
Gemini：x-goog-api-key），不放进 URL（URL 会进 httpx 的日志和错误信息）。可灵用 AccessKey + SecretKey 时，
每个请求现签一个 JWT（KlingJwtAuth），只放请求头，不进任何文件。所有错误信息都过一遍 scrub()，
接口回显的 Key 换成掩码、像 JWT 的串整个隐藏。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from collections.abc import Callable, Generator

import httpx

from backend.domain.api_keys import SERVICES, Creds, scrub

ANTHROPIC_VERSION = "2023-06-01"
KLING_JWT_TTL = 1800      # 可灵文档：exp = 现在 + 1800 秒，nbf = 现在 - 5 秒
KLING_JWT_SKEW = 5


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def kling_jwt(access_key: str, secret_key: str, *, now: float | None = None) -> str:
    """可灵开放平台的鉴权令牌：HS256 JWT，header {alg: HS256, typ: JWT}，
    payload {iss: AccessKey, exp: now+1800, nbf: now-5}，用 SecretKey 签名（和官方示例里 PyJWT 的写法一样，
    这里用标准库实现，不加依赖）。now 给测试固定时钟用。"""
    t = int(time.time() if now is None else now)
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {"iss": access_key, "exp": t + KLING_JWT_TTL, "nbf": t - KLING_JWT_SKEW}
    signing = (_b64url(json.dumps(header, separators=(",", ":")).encode())
               + "." + _b64url(json.dumps(payload, separators=(",", ":")).encode()))
    sig = hmac.new(secret_key.encode(), signing.encode("ascii"), hashlib.sha256).digest()
    return signing + "." + _b64url(sig)


class KlingJwtAuth(httpx.Auth):
    """每个请求现签一个 JWT（轮询能跑很久，不用一个令牌撑全程）。clock 给测试用。"""

    def __init__(self, access_key: str, secret_key: str, clock: Callable[[], float] | None = None):
        self._ak, self._sk = access_key, secret_key
        self._clock = clock or time.time

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        request.headers["Authorization"] = f"Bearer {kling_jwt(self._ak, self._sk, now=self._clock())}"
        yield request


def auth_headers(creds: Creds) -> dict[str, str]:
    """固定的认证头。可灵用 AK/SK 时这里不放（见 client() 里的 KlingJwtAuth）。"""
    key = creds.require()
    if creds.service in ("openai", "ark"):
        return {"Authorization": f"Bearer {key}"}
    if creds.service == "kling":
        return {} if creds.aksk else {"Authorization": f"Bearer {key}"}
    if creds.service == "anthropic":
        return {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION}
    return {"x-goog-api-key": key}


def client(creds: Creds, *, timeout: float = 300.0,
           transport: httpx.BaseTransport | None = None) -> httpx.Client:
    """读环境变量里的代理（HTTPS_PROXY）：要走代理才能访问这几家的用户设它就行。
    注入了 transport（测试）时不读，免得代理设置把假 transport 绕过去。"""
    auth = (KlingJwtAuth(creds.access_key or "", creds.secret_key or "")
            if creds.service == "kling" and creds.aksk else None)
    return httpx.Client(base_url=creds.base_url, timeout=timeout, headers=auth_headers(creds), auth=auth,
                        transport=transport, follow_redirects=True, trust_env=transport is None)


def error_detail(r: httpx.Response) -> str:
    """响应体里的错误说明：{error: {message}}（OpenAI / Anthropic / Gemini / 火山方舟都是这个形状）；
    可灵是 {code, message}。"""
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
            code = d.get("code")
            tail = f"（code {code}）" if code not in (None, 0, "") and str(code) not in str(d["message"]) else ""
            return f"{d['message']}{tail}"[:400]
    return (r.text or "").strip()[:300]


def error_code(r: httpx.Response) -> str | None:
    """厂商自己的错误码：火山方舟 error.code（字符串），可灵顶层 code（数字）。"""
    try:
        d = r.json()
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(d, dict):
        return None
    e = d.get("error")
    if isinstance(e, dict) and e.get("code"):
        return str(e["code"])
    if d.get("code") not in (None, 0, ""):
        return str(d["code"])
    return None


# 火山方舟的错误码（error.code）→ 中文说明。来源：火山方舟「错误码」文档（2026-10 核对过名字，未实跑）
ARK_HINTS = {
    "AuthenticationError": "API Key 不对或已删除。到「设置 → API 服务」检查",
    "AccountOverdueError": "账户欠费或余额不足：到火山方舟控制台充值后重试",
    "AccessDenied": "这个 Key 没有权限访问这个模型或接口",
    "ModelNotOpen": "这个模型还没在火山方舟控制台「开通管理」里开通",
    "InvalidEndpointOrModel.NotFound": "模型名不对，或这个区域没有这个模型（看看区域 / Base URL）",
    "InputTextSensitiveContentDetected": "提示词没通过内容审核：改写提示词后重试",
    "InputImageSensitiveContentDetected": "首帧 / 尾帧图片没通过内容审核：换一张图后重试",
    "OutputVideoSensitiveContentDetected": "生成的视频没通过内容审核：改写提示词或换首帧后重试",
    "SetLimitExceeded": "达到了控制台里设的推理限额（安心体验模式）：到控制台调高限额",
    "QuotaExceeded": "额度用完了：稍后再试，或到控制台检查配额",
    "RateLimitExceeded": "请求太快被限流：稍后再试",
    "ServerOverloaded": "服务繁忙：稍后再试",
}
# 可灵的错误码（顶层 code）→ 中文说明。来源：可灵开放平台「错误码」（2026-10 按公开文档整理，未实跑）
KLING_HINTS = {
    "1000": "鉴权失败：API Key 或 AccessKey / SecretKey 不对",
    "1001": "鉴权失败：请求里没有 Authorization",
    "1002": "鉴权失败：API Key 或签名不对（AccessKey / SecretKey 填反了、或复制不全）",
    "1003": "鉴权失败：令牌还没到生效时间（本机时钟比服务器快，校准系统时间）",
    "1004": "鉴权失败：令牌已过期（本机时钟比服务器慢，校准系统时间）",
    "1100": "账户异常：到可灵开放平台检查账户状态",
    "1101": "账户欠费：充值后重试",
    "1102": "资源包用完或已过期（余额不足）：到可灵开放平台购买资源包后重试",
    "1103": "这个账号没有这个模型 / 接口的权限",
    "1200": "请求参数不对",
    "1201": "请求参数不对（模型、时长、模式的组合可能不支持）",
    "1203": "模型或资源不存在：模型名可能写错了",
    "1300": "触发平台策略",
    "1301": "内容没通过安全审核：改写提示词或换首帧后重试",
    "1302": "请求太快被限流：稍后再试",
    "1303": "并发或 QPS 超过限制：等前面的任务跑完再试",
    "1304": "IP 不在白名单里：到可灵开放平台检查 IP 白名单",
    "5000": "服务端出错，稍后再试",
    "5001": "服务暂时不可用，稍后再试",
    "5002": "服务维护中，稍后再试",
}
VENDOR_HINTS = {"ark": ARK_HINTS, "kling": KLING_HINTS}


def vendor_hint(service_id: str, code: str | None) -> str | None:
    if not code:
        return None
    hints = VENDOR_HINTS.get(service_id) or {}
    return hints.get(code) or next((v for k, v in hints.items() if code.startswith(k)), None)


def http_error_text(service_id: str, r: httpx.Response, creds: Creds | None = None,
                    model: str | None = None) -> str:
    vendor = SERVICES[service_id].vendor
    code = r.status_code
    detail = scrub(error_detail(r), *(creds.secrets() if creds else ()))
    tail = f"：{detail}" if detail else ""
    hint = vendor_hint(service_id, error_code(r))
    if hint:
        return f"{vendor}（HTTP {code}）：{hint}{tail}"
    if code in (401, 403):
        what = "API Key / 签名" if service_id == "kling" else "API Key"
        why = (f"{vendor} 拒绝了 {what}（HTTP {code}）：Key 不对、过期，或者没有这个模型的权限。"
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
                 "。检查网络 / 代理（HTTPS_PROXY）或 Base URL", *creds.secrets())
