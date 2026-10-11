"""API 服务（OpenAI / Anthropic / Google Gemini）的 Key、Base URL 和模型名。

存在 app_settings 表里（key = "api:<服务>"，值是 JSON：{api_key, base_url, models: {text, image, video}}），
不用新表、不用迁移；SettingsStore.load 只认 text_model / image_engine / video_engine / step:*，
不会把这些行带进 GET /api/settings。每次用的时候现读，改了不用重启。

Key 的来源：设置页填的优先，没填再看环境变量（OPENAI_API_KEY / ANTHROPIC_API_KEY / GEMINI_API_KEY 或
GOOGLE_API_KEY）。明文 Key 只在发请求时放进请求头；任何响应、日志、错误信息里只出现掩码（sk-…abcd）。
"""
from __future__ import annotations

import os
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
    llm_provider: str                 # 对应的文本模型 provider id（见 backend/llm/api_providers.py）
    engines: tuple[str, ...] = ()     # 用这家 Key 的出图/视频引擎


# 模型名默认值：常量，用户可以在设置页改（存 api:<服务>.models）
OPENAI_TEXT_MODEL = "gpt-4.1"
OPENAI_IMAGE_MODEL = "gpt-image-1"
OPENAI_VIDEO_MODEL = "sora-2"
ANTHROPIC_TEXT_MODEL = "claude-sonnet-4-5"
GEMINI_TEXT_MODEL = "gemini-2.5-flash"
GEMINI_IMAGE_MODEL = "gemini-2.5-flash-image"
GEMINI_VIDEO_MODEL = "veo-3.0-generate-001"

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
}


def mask(key: str | None) -> str | None:
    """sk-…abcd：只露前 3 位和后 4 位；太短的 Key 只露后 2 位。"""
    if not key:
        return None
    if len(key) >= 12:
        return f"{key[:3]}…{key[-4:]}"
    return f"…{key[-2:]}" if len(key) > 4 else "…"


def scrub(text: str, *keys: str | None) -> str:
    """把错误信息里出现的 Key 换成掩码（有的接口会把收到的 Key 回显在报错里）。"""
    out = str(text or "")
    for k in keys:
        if k and len(k) >= 6:
            out = out.replace(k, mask(k) or "…")
    return out


def no_key_hint(service_id: str) -> str:
    s = SERVICES[service_id]
    env = " 或 ".join(s.env_keys)
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

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def model(self, kind: str, fallback: str | None = None) -> str:
        """生效的模型名：设置页填的 → fallback（config.yaml 里写的）→ 默认常量。"""
        if kind in self.explicit and self.models.get(kind):
            return self.models[kind]
        return fallback or self.models.get(kind) or SERVICES[self.service].models.get(kind, "")

    def require(self) -> str:
        if not self.api_key:
            raise MissingKey(no_key_hint(self.service))
        return self.api_key


class MissingKey(RuntimeError):
    """没有 Key：信息给用户看（中文、不含任何 Key）。"""


def _env_key(svc: ApiService) -> tuple[str | None, str | None]:
    for name in svc.env_keys:
        v = (os.environ.get(name) or "").strip()
        if v:
            return v, name
    return None, None


def build_creds(service_id: str, row: dict | None) -> Creds:
    svc = SERVICES[service_id]
    row = row if isinstance(row, dict) else {}
    key = (row.get("api_key") or "").strip() or None
    source, env_name = ("settings", None) if key else (None, None)
    if not key:
        key, env_name = _env_key(svc)
        source = "env" if key else None
    base = (row.get("base_url") or "").strip()
    if not base and svc.env_base:
        base = (os.environ.get(svc.env_base) or "").strip()
    base = (base or svc.default_base).rstrip("/")
    saved = row.get("models") if isinstance(row.get("models"), dict) else {}
    models = {k: (str(saved.get(k) or "").strip() or d) for k, d in svc.models.items()}
    explicit = frozenset(k for k in svc.models if str(saved.get(k) or "").strip())
    return Creds(service_id, key, base, source, env_name, models, explicit)


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
               base_url: str | None = None, models: dict[str, str | None] | None = None) -> None:
        """api_key：None / 空串 = 不改；clear_key = 删掉设置页存的 Key（环境变量照样能用）。
        base_url：None = 不改，空串 = 恢复默认。models：只改给了的，空串 / None = 恢复默认。"""
        if service_id not in SERVICES:
            raise KeyError(f"没有这个 API 服务：{service_id}")
        row = self._row(service_id)
        if clear_key:
            row.pop("api_key", None)
        elif api_key:
            row["api_key"] = api_key.strip()
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
        return {
            "id": svc.id, "label": svc.label,
            "configured": c.configured, "source": c.source, "env_name": c.env_name,
            "env_keys": list(svc.env_keys),
            "masked": mask(c.api_key),
            "has_saved_key": bool((row.get("api_key") or "").strip()),
            "base_url": (row.get("base_url") or None), "effective_base_url": c.base_url,
            "default_base_url": svc.default_base, "base_hint": svc.base_hint,
            "models": {k: {"value": (saved_models.get(k) or None), "effective": c.model(k),
                           "default": d, "label": KIND_LABEL[k],
                           "suggestions": list(svc.suggestions.get(k, ()))}
                       for k, d in svc.models.items()},
            "llm_provider": svc.llm_provider, "engines": list(svc.engines),
            "last_test": self.last_test.get(service_id),
        }

    def all_public(self) -> list[dict[str, Any]]:
        return [self.public(k) for k in SERVICES]
