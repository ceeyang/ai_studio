"""API 服务（OpenAI / Anthropic / Google Gemini / 火山方舟 / 可灵）的 Key、Base URL、区域和模型名。

存在 app_settings 表里（key = "api:<服务>"，值是 JSON：{api_key, base_url, models: {text, image, video}}，
有区域的服务多一个 region（cn / intl），可灵多 access_key / secret_key），不用新表、不用迁移；SettingsStore.load 只认 text_model / image_engine / video_engine / step:*，
不会把这些行带进 GET /api/settings。每次用的时候现读，改了不用重启。

Key 的来源：设置页填的优先，没填再看环境变量（OPENAI_API_KEY / ANTHROPIC_API_KEY / GEMINI_API_KEY 或
GOOGLE_API_KEY / ARK_API_KEY / KLING_API_KEY，可灵的 AK/SK 是 KLING_ACCESS_KEY + KLING_SECRET_KEY）。
明文 Key 只在发请求时放进请求头；任何响应、日志、错误信息里只出现掩码（sk-…abcd）。

区域（火山方舟、可灵）：国内 / 海外各有一个官方地址，区域只决定默认 Base URL；用户自己填了 Base URL 以填的为准。
可灵两种认证都收：填了 API Key 用 API Key（Bearer），否则用 AccessKey + SecretKey 每次请求现签一个
短期 JWT（backend/llm/api_http.py 的 kling_jwt），JWT 不落盘。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from backend.domain.models import AppSetting, utcnow

KEY_PREFIX = "api:"
MODEL_KINDS = ("text", "image", "video")
KIND_LABEL = {"text": "文本模型", "image": "出图模型", "video": "视频模型"}


@dataclass(frozen=True)
class ApiService:
    id: str
    label: str
    vendor: str                       # 错误信息里的叫法
    env_keys: tuple[str, ...]
    env_base: str | None
    default_base: str
    base_hint: str
    models: dict[str, str]            # 能力 → 默认模型名（只列这家支持的能力）
    suggestions: dict[str, tuple[str, ...]]
    llm_provider: str                 # 对应的文本模型 provider id（见 backend/llm/api_providers.py），没有就是空串
    engines: tuple[str, ...] = ()     # 用这家 Key 的出图/视频引擎
    # 区域 → (中文名, 官方 Base URL)；空 = 没有区域选项。第一个是默认区域
    regions: dict[str, tuple[str, str]] = field(default_factory=dict)
    # 除了 API Key 还收 AccessKey + SecretKey（可灵）：两个环境变量名
    env_aksk: tuple[str, str] | None = None


# 模型名默认值：常量，用户可以在设置页改（存 api:<服务>.models）
OPENAI_TEXT_MODEL = "gpt-4.1"
OPENAI_IMAGE_MODEL = "gpt-image-1"
OPENAI_VIDEO_MODEL = "sora-2"
ANTHROPIC_TEXT_MODEL = "claude-sonnet-4-5"
GEMINI_TEXT_MODEL = "gemini-2.5-flash"
GEMINI_IMAGE_MODEL = "gemini-2.5-flash-image"
GEMINI_VIDEO_MODEL = "veo-3.0-generate-001"
# Seedance 2.0 / 可灵 v3：模型 id 怎么核对的见 backend/engines/api/engines.py 的 SeedanceEngine / KlingEngine
ARK_VIDEO_MODEL = "doubao-seedance-2-0-260128"
KLING_VIDEO_MODEL = "kling-v3"

ARK_REGIONS = {"cn": ("国内（火山方舟 · 北京）", "https://ark.cn-beijing.volces.com/api/v3"),
               "intl": ("海外（BytePlus ModelArk · 新加坡）", "https://ark.ap-southeast.bytepluses.com/api/v3")}
KLING_REGIONS = {"cn": ("国内（可灵开放平台 · 北京）", "https://api-beijing.klingai.com"),
                 "intl": ("海外（Kling AI 国际版 · 新加坡）", "https://api-singapore.klingai.com")}

SERVICES: dict[str, ApiService] = {
    "openai": ApiService(
        id="openai", label="OpenAI（GPT）", vendor="OpenAI",
        env_keys=("OPENAI_API_KEY",), env_base="OPENAI_BASE_URL",
        default_base="https://api.openai.com/v1",
        base_hint="OpenAI 兼容代理 / 中转填到 /v1 为止，例如 https://api.openai.com/v1",
        models={"text": OPENAI_TEXT_MODEL, "image": OPENAI_IMAGE_MODEL, "video": OPENAI_VIDEO_MODEL},
        suggestions={"text": ("gpt-4.1", "gpt-4.1-mini", "gpt-4o", "gpt-5", "gpt-5-mini"),
                     "image": ("gpt-image-1", "gpt-image-1-mini"),
                     "video": ("sora-2", "sora-2-pro")},
        llm_provider="openai_api", engines=("openai_image", "sora_video")),
    "anthropic": ApiService(
        id="anthropic", label="Anthropic（Claude）", vendor="Anthropic",
        env_keys=("ANTHROPIC_API_KEY",), env_base="ANTHROPIC_BASE_URL",
        default_base="https://api.anthropic.com",
        base_hint="不带 /v1，例如 https://api.anthropic.com",
        models={"text": ANTHROPIC_TEXT_MODEL},
        suggestions={"text": ("claude-sonnet-4-5", "claude-opus-4-1", "claude-haiku-4-5")},
        llm_provider="anthropic_api"),
    "gemini": ApiService(
        id="gemini", label="Google（Gemini）", vendor="Gemini",
        env_keys=("GEMINI_API_KEY", "GOOGLE_API_KEY"), env_base=None,
        default_base="https://generativelanguage.googleapis.com",
        base_hint="不带 /v1beta，例如 https://generativelanguage.googleapis.com",
        models={"text": GEMINI_TEXT_MODEL, "image": GEMINI_IMAGE_MODEL, "video": GEMINI_VIDEO_MODEL},
        suggestions={"text": ("gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.5-flash-lite"),
                     "image": ("gemini-2.5-flash-image",),
                     "video": ("veo-3.0-generate-001", "veo-3.0-fast-generate-001",
                               "veo-3.1-generate-preview", "veo-3.1-fast-generate-preview")},
        llm_provider="gemini_api", engines=("gemini_api_image", "veo_video")),
    "ark": ApiService(
        id="ark", label="火山方舟（Seedance）", vendor="火山方舟",
        env_keys=("ARK_API_KEY",), env_base="ARK_BASE_URL",
        default_base=ARK_REGIONS["cn"][1],
        base_hint="填到 /api/v3 为止，例如 https://ark.cn-beijing.volces.com/api/v3",
        models={"video": ARK_VIDEO_MODEL},
        suggestions={"video": ("doubao-seedance-2-0-260128", "doubao-seedance-2-0-fast-260128",
                               "doubao-seedance-2-5-260628", "doubao-seedance-1-5-pro-251215")},
        llm_provider="", engines=("seedance_video",), regions=ARK_REGIONS),
    "kling": ApiService(
        id="kling", label="可灵（Kling）", vendor="可灵",
        env_keys=("KLING_API_KEY",), env_base="KLING_BASE_URL",
        default_base=KLING_REGIONS["cn"][1],
        base_hint="不带 /v1，例如 https://api-beijing.klingai.com",
        models={"video": KLING_VIDEO_MODEL},
        suggestions={"video": ("kling-v3", "kling-v2-6", "kling-v2-5-turbo", "kling-v2-1")},
        llm_provider="", engines=("kling_video",), regions=KLING_REGIONS,
        env_aksk=("KLING_ACCESS_KEY", "KLING_SECRET_KEY")),
}


def mask(key: str | None) -> str | None:
    """sk-…abcd：只露前 3 位和后 4 位；太短的 Key 只露后 2 位。"""
    if not key:
        return None
    if len(key) >= 12:
        return f"{key[:3]}…{key[-4:]}"
    return f"…{key[-2:]}" if len(key) > 4 else "…"


# 像 JWT 的串（header.payload.signature，前两段是 base64url 的 JSON，以 eyJ 开头）：可灵 AK/SK 现签的令牌
JWT_RE = re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}")


def scrub(text: str, *keys: str | None) -> str:
    """把错误信息里出现的 Key 换成掩码（有的接口会把收到的 Key 回显在报错里）；像 JWT 的串一律隐藏。"""
    out = str(text or "")
    for k in keys:
        if k and len(k) >= 6:
            out = out.replace(k, mask(k) or "…")
    return JWT_RE.sub("eyJ…（令牌已隐藏）", out)


def no_key_hint(service_id: str) -> str:
    s = SERVICES[service_id]
    env = " 或 ".join(s.env_keys)
    if s.env_aksk:
        return (f"还没有配置 {s.label} 的 API Key 或 AccessKey + SecretKey：到「设置 → API 服务」填上"
                f"（或设置环境变量 {env}，或 {s.env_aksk[0]} + {s.env_aksk[1]}，然后重启 studio）")
    return (f"还没有配置 {s.label} 的 API Key：到「设置 → API 服务」填上"
            f"（或设置环境变量 {env} 后重启 studio）")


@dataclass
class Creds:
    """一家服务现在生效的 Key / Base URL / 模型名。repr 不带明文 Key。"""
    service: str
    api_key: str | None = field(default=None, repr=False)
    base_url: str = ""
    source: str | None = None          # settings / env / None（没配）
    env_name: str | None = None        # source=env 时是哪个环境变量
    models: dict[str, str] = field(default_factory=dict)
    explicit: frozenset[str] = frozenset()    # 设置页明确填了模型名的能力（text / image / video）
    region: str | None = None                 # 有区域选项的服务：cn / intl
    # 可灵的 AccessKey + SecretKey（没有 API Key 时用来签 JWT）
    access_key: str | None = field(default=None, repr=False)
    secret_key: str | None = field(default=None, repr=False)

    @property
    def aksk(self) -> bool:
        """用 AccessKey + SecretKey 签名（没有 API Key、AK 和 SK 都有）。"""
        return not self.api_key and bool(self.access_key and self.secret_key)

    @property
    def configured(self) -> bool:
        return bool(self.api_key) or self.aksk

    def secrets(self) -> tuple[str, ...]:
        """要从错误信息、日志、记录里掩掉的原文（API Key、AccessKey、SecretKey）。"""
        return tuple(k for k in (self.api_key, self.access_key, self.secret_key) if k)

    def model(self, kind: str, fallback: str | None = None) -> str:
        """生效的模型名：设置页填的 → fallback（config.yaml 里写的）→ 默认常量。"""
        if kind in self.explicit and self.models.get(kind):
            return self.models[kind]
        return fallback or self.models.get(kind) or SERVICES[self.service].models.get(kind, "")

    def require(self) -> str:
        """返回 API Key；可灵用 AK/SK 时返回 AccessKey（签名在 api_http.auth_headers 里做）。"""
        if not self.configured:
            raise MissingKey(no_key_hint(self.service))
        return self.api_key or self.access_key or ""


class MissingKey(RuntimeError):
    """没有 Key：信息给用户看（中文、不含任何 Key）。"""


def _env_key(svc: ApiService) -> tuple[str | None, str | None]:
    for name in svc.env_keys:
        v = (os.environ.get(name) or "").strip()
        if v:
            return v, name
    return None, None


def region_of(svc: ApiService, row: dict) -> str | None:
    """生效的区域：设置页选的（认得的）→ 第一个（国内）。没有区域选项的服务是 None。"""
    if not svc.regions:
        return None
    r = str(row.get("region") or "").strip()
    return r if r in svc.regions else next(iter(svc.regions))


def default_base_of(svc: ApiService, region: str | None) -> str:
    """区域对应的官方地址（没有区域选项的服务就是 default_base）。"""
    return svc.regions[region][1] if region and region in svc.regions else svc.default_base


def _pair(ak: str | None, sk: str | None) -> tuple[str | None, str | None]:
    ak, sk = (ak or "").strip() or None, (sk or "").strip() or None
    return (ak, sk) if ak and sk else (None, None)


def build_creds(service_id: str, row: dict | None) -> Creds:
    """Key 的优先级：设置页的 API Key → 设置页的 AK+SK → 环境变量的 API Key → 环境变量的 AK+SK
    （同一处两种都有时 API Key 优先）。Base URL：设置页填的 → 环境变量 → 区域的官方地址。"""
    svc = SERVICES[service_id]
    row = row if isinstance(row, dict) else {}
    key = (row.get("api_key") or "").strip() or None
    ak = sk = None
    if svc.env_aksk and not key:
        ak, sk = _pair(row.get("access_key"), row.get("secret_key"))
    source, env_name = ("settings", None) if (key or ak) else (None, None)
    if not (key or ak):
        key, env_name = _env_key(svc)
        if not key and svc.env_aksk:
            ak, sk = _pair(os.environ.get(svc.env_aksk[0]), os.environ.get(svc.env_aksk[1]))
            env_name = f"{svc.env_aksk[0]} + {svc.env_aksk[1]}" if ak else None
        source = "env" if (key or ak) else None
    region = region_of(svc, row)
    base = (row.get("base_url") or "").strip()
    if not base and svc.env_base:
        base = (os.environ.get(svc.env_base) or "").strip()
    base = (base or default_base_of(svc, region)).rstrip("/")
    saved = row.get("models") if isinstance(row.get("models"), dict) else {}
    models = {k: (str(saved.get(k) or "").strip() or d) for k, d in svc.models.items()}
    explicit = frozenset(k for k in svc.models if str(saved.get(k) or "").strip())
    return Creds(service_id, key, base, source, env_name, models, explicit, region, ak, sk)


def env_creds(service_id: str) -> Creds:
    """没有数据库时（CLI、测试）：只看环境变量。"""
    return build_creds(service_id, None)


class ApiKeyStore:
    def __init__(self, db):
        self.db = db
        # 最近一次「测试连接」的结果（只在内存里，给环境检查显示）：服务 → {ok, message}
        self.last_test: dict[str, dict[str, Any]] = {}

    def _row(self, service_id: str) -> dict:
        with self.db.session() as s:
            r = s.get(AppSetting, KEY_PREFIX + service_id)
            return dict(r.value) if r is not None and isinstance(r.value, dict) else {}

    def resolve(self, service_id: str) -> Creds:
        if service_id not in SERVICES:
            raise KeyError(f"没有这个 API 服务：{service_id}，可选：{', '.join(SERVICES)}")
        return build_creds(service_id, self._row(service_id))

    __call__ = resolve

    def update(self, service_id: str, *, api_key: str | None = None, clear_key: bool = False,
               base_url: str | None = None, models: dict[str, str | None] | None = None,
               region: str | None = None, access_key: str | None = None,
               secret_key: str | None = None) -> None:
        """api_key / access_key / secret_key：None / 空串 = 不改；clear_key = 删掉设置页存的所有 Key
        （API Key 和 AK/SK 一起删；同一次请求里又给了新的就先删再存；环境变量照样能用）。
        base_url：None = 不改，空串 = 恢复默认。models：只改给了的，空串 / None = 恢复默认。
        region：None = 不改，空串 / 不认得 = 恢复默认（国内）。"""
        if service_id not in SERVICES:
            raise KeyError(f"没有这个 API 服务：{service_id}")
        svc = SERVICES[service_id]
        row = self._row(service_id)
        if clear_key:
            for k in ("api_key", "access_key", "secret_key"):
                row.pop(k, None)
        if api_key:
            row["api_key"] = api_key.strip()
        if svc.env_aksk:
            if access_key:
                row["access_key"] = access_key.strip()
            if secret_key:
                row["secret_key"] = secret_key.strip()
        if region is not None and svc.regions:
            if region.strip() in svc.regions:
                row["region"] = region.strip()
            else:
                row.pop("region", None)
        if base_url is not None:
            if base_url.strip():
                row["base_url"] = base_url.strip().rstrip("/")
            else:
                row.pop("base_url", None)
        if models is not None:
            cur = dict(row.get("models") or {})
            for k, v in models.items():
                if (v or "").strip():
                    cur[k] = v.strip()
                else:
                    cur.pop(k, None)
            if cur:
                row["models"] = cur
            else:
                row.pop("models", None)
        key = KEY_PREFIX + service_id
        with self.db.session() as s:
            if not row:
                s.execute(delete(AppSetting).where(AppSetting.key == key))
                return
            now = utcnow()
            stmt = sqlite_insert(AppSetting).values(key=key, value=row, updated_at=now)
            s.execute(stmt.on_conflict_do_update(index_elements=[AppSetting.key],
                                                 set_={"value": row, "updated_at": now}))
        self.last_test.pop(service_id, None)

    def public(self, service_id: str) -> dict[str, Any]:
        """给前端看的：只有掩码，没有明文 Key。"""
        svc = SERVICES[service_id]
        row = self._row(service_id)
        c = build_creds(service_id, row)
        saved_models = row.get("models") if isinstance(row.get("models"), dict) else {}
        region = region_of(svc, row)
        has_aksk = bool((row.get("access_key") or "").strip() and (row.get("secret_key") or "").strip())
        return {
            "id": svc.id, "label": svc.label,
            "configured": c.configured, "source": c.source, "env_name": c.env_name,
            "env_keys": list(svc.env_keys),
            # 用 AK/SK 签名时掩码是 AccessKey 的
            "masked": mask(c.api_key or c.access_key),
            "has_saved_key": bool((row.get("api_key") or "").strip()) or has_aksk,
            "base_url": (row.get("base_url") or None), "effective_base_url": c.base_url,
            "default_base_url": default_base_of(svc, region), "base_hint": svc.base_hint,
            # 区域：没有区域选项的服务 regions 是空列表、region 是 null
            "regions": [{"id": k, "label": lab, "base_url": url} for k, (lab, url) in svc.regions.items()],
            "region": region,
            # 可灵：除了 API Key 还收 AccessKey + SecretKey（env_aksk 是两个环境变量名）
            "aksk": bool(svc.env_aksk), "env_aksk": list(svc.env_aksk or ()),
            "auth": ("aksk" if c.aksk else "api_key") if c.configured else None,
            "has_saved_aksk": has_aksk,
            "models": {k: {"value": (saved_models.get(k) or None), "effective": c.model(k),
                           "default": d, "label": KIND_LABEL[k],
                           "suggestions": list(svc.suggestions.get(k, ()))}
                       for k, d in svc.models.items()},
            "llm_provider": svc.llm_provider, "engines": list(svc.engines),
            "last_test": self.last_test.get(service_id),
        }

    def all_public(self) -> list[dict[str, Any]]:
        return [self.public(k) for k in SERVICES]
