"""网页会话续接：同一个作用域在同一个网站上重新生成时，接着上次那条网页对话发，而不是每次开新对话。

作用域（同一站点内各自一条对话）：
- 出图（gpt_image / gemini_image）：角色的定妆照、三视图等所有图 → character:<角色 id>；镜头首帧 → shot:<镜头 id>；
  场景图 → location:<id>；道具图 → prop:<id>。
- 网页文本模型（web_chatgpt / web_gemini / web_claude）：按集、按步骤 → episode:<集 id>:<步骤 key>。
  剧本两步各一条（script_structure / script_visual）；分镜是按段落调用的（每段一次 LLM 调用，key 是
  storyboard:<段落 id>），所以每段一条对话：重写某段分镜就接着这段上次的对话，几十段不会挤进同一条对话里
  很快超过轮数上限；推荐角色按集一条。写梗概、推荐造型等没归属到集的步骤不续接（每次新对话）。

队列执行任务时（JobManager._execute）按任务类型和 payload 算出作用域，用 use_scope 装到当前线程上；
BrowserRunner（出图）和 WebChatProvider（文本）从 current_scope() 取。payload 里 new_conversation=true
（界面上勾了「新开对话」）时这次不续接、新开一条并把记录换成新的。

什么时候不续接、新开：没有作用域；这次勾了新开对话；设置里关了「重新生成时续接之前的网页会话」；
这个作用域在这个网站上还没有记录；已经发过 MAX_TURNS 轮；页面上那条对话打不开（跳到首页 / 登录页、
被删了，由页面流程判断后退回新对话并说明原因）。每次网页调用结束后读标签页地址，认出对话 id 就记下来。
"""
from __future__ import annotations

import contextvars
import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select

log = logging.getLogger(__name__)

MAX_TURNS = 20               # 一条对话发过这么多轮就新开（太长的对话网页会变慢、模型也容易被前文带偏）
SETTING_KEY = "web_reuse_conversation"
IMAGE_KINDS = ("character", "shot", "location", "prop")
TEXT_KINDS = ("episode",)
# 续接时加在出图提示词前面：别让网站把这次当成「改上一张图」
CONTINUE_IMAGE_PREFIX = ("Generate a NEW image for this request (do not edit or reuse the previous image "
                         "unless asked; this request is complete on its own).")
# 续接时加在文本提示词前面：这是一个新的、完整的请求
CONTINUE_TEXT_PREFIX = ("【新的请求】下面是一个新的、完整的任务（不是对上一条回答的修改）："
                        "按这里的全部要求重新完整作答，不要只写改动的部分，也不要沿用上一次回答的格式以外的内容。")

# 任务类型 → (作用域类别, payload 里 id 的键)
IMAGE_JOB_SCOPES: dict[str, tuple[str, str]] = {
    "image.portrait": ("character", "character_id"),
    "image.turnaround": ("character", "character_id"),
    "image.keyframe": ("shot", "shot_id"),
    "image.location": ("location", "location_id"),
    "image.prop": ("prop", "prop_id"),
}
# 文本任务类型 → payload 里集 id 的键（作用域再按 LLM 调用的 key 细分到步骤 / 段落）
TEXT_JOB_SCOPES: dict[str, str] = {
    "script": "episode_id",
    "storyboard": "episode_id",
    "character.recommend": "episode_id",
}


@dataclass(frozen=True)
class Scope:
    kind: str                       # character / shot / location / prop / episode
    id: str                         # 角色 id；episode 是「集 id」或「集 id:步骤 key」
    project_id: int | None = None
    fresh: bool = False             # 这次强制新开对话（界面上勾了「新开对话」）

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.id}"

    @property
    def for_images(self) -> bool:
        return self.kind in IMAGE_KINDS

    @property
    def for_text(self) -> bool:
        return self.kind in TEXT_KINDS

    def child(self, sub: str) -> Scope:
        """文本模型按 LLM 调用的 key 细分：episode:5 → episode:5:storyboard:12。"""
        sub = (sub or "").strip()
        return replace(self, id=f"{self.id}:{sub}") if sub else self


_current: contextvars.ContextVar[Scope | None] = contextvars.ContextVar("web_conversation_scope", default=None)


def current_scope() -> Scope | None:
    return _current.get()


@contextmanager
def use_scope(scope: Scope | None) -> Iterator[Scope | None]:
    token = _current.set(scope)
    try:
        yield scope
    finally:
        _current.reset(token)


def scope_for_job(job_type: str, payload: dict | None, project_id: int | None = None) -> Scope | None:
    """任务 → 作用域；没有归属（写梗概等）返回 None（不续接）。"""
    p = payload or {}
    fresh = bool(p.get("new_conversation"))
    if job_type in IMAGE_JOB_SCOPES:
        kind, field = IMAGE_JOB_SCOPES[job_type]
        if p.get(field) is None:
            return None
        return Scope(kind, str(p[field]), project_id, fresh)
    if job_type in TEXT_JOB_SCOPES:
        ep = p.get(TEXT_JOB_SCOPES[job_type])
        if ep is None:
            return None
        return Scope("episode", str(ep), project_id, fresh)
    return None


def conversation_id(spec: Any, url: str | None) -> str:
    """网址是这个网站上的一条对话时返回对话 id，否则空串（首页、新对话页、登录页、别的网站）。"""
    if not url or not getattr(spec, "conversation_re", ""):
        return ""
    try:
        u = urlparse(url)
    except ValueError:
        return ""
    host = (u.hostname or "").lower()
    hosts = {spec.host, *getattr(spec, "extra", {}).get("hosts", ())}
    if not any(host == h or host.endswith("." + h) for h in hosts if h):
        return ""
    m = re.search(spec.conversation_re, u.path or "")
    return m.group(1) if m else ""


@dataclass
class Plan:
    """这次网页调用打算怎么开对话。url 非空 = 续接这条；空 = 新开（reason 说明为什么）。"""
    site: str
    scope: Scope | None
    url: str | None = None
    reason: str = ""
    turns: int = 0                  # 续接的那条对话已经发过几轮

    @property
    def continuing(self) -> bool:
        return bool(self.url)

    def fallback(self, reason: str) -> None:
        """续接不了（扩展太旧、页面上那条对话打不开）：改成新开。"""
        self.url, self.reason, self.turns = None, reason, 0


def _iso(dt) -> str | None:
    return dt.isoformat() + "Z" if dt else None


class ConversationBook:
    """browser_conversations 表的读写和续接 / 新开的判断。settings 是 SettingsStore（全局开关）。"""

    def __init__(self, db: Any, settings: Any = None, *, max_turns: int = MAX_TURNS):
        self.db = db
        self.settings = settings
        self.max_turns = max_turns

    # ------------------------------------------------------------ 设置
    def enabled(self) -> bool:
        if self.settings is None:
            return True
        try:
            return bool(self.settings.load().web_reuse_conversation)
        except Exception:
            log.exception("读「续接网页会话」设置失败，按开着算")
            return True

    # ------------------------------------------------------------ 读
    def get(self, site: str, scope: Scope) -> dict | None:
        from backend.domain.models import BrowserConversation as BC
        with self.db.session() as s:
            r = s.scalar(select(BC).where(BC.site == site, BC.scope_kind == scope.kind, BC.scope_id == scope.id))
            return self._row(r) if r is not None else None

    def list(self, scope_kind: str, scope_id: str) -> list[dict]:
        from backend.domain.models import BrowserConversation as BC
        with self.db.session() as s:
            rows = s.scalars(select(BC).where(BC.scope_kind == scope_kind, BC.scope_id == str(scope_id))
                             .order_by(BC.site))
            return [self._row(r) for r in rows if r.url]

    @staticmethod
    def _row(r) -> dict:
        return {"site": r.site, "scope_kind": r.scope_kind, "scope_id": r.scope_id, "project_id": r.project_id,
                "url": r.url, "conversation_id": r.conversation_id, "turns": r.turns,
                "created_at": _iso(r.created_at), "updated_at": _iso(r.updated_at)}

    # ------------------------------------------------------------ 判断
    def plan(self, site: str, scope: Scope | None) -> Plan:
        if scope is None:
            return Plan(site, None, reason="这一步不续接网页会话")
        if scope.fresh:
            return Plan(site, scope, reason="这次选了「新开对话」")
        if not self.enabled():
            return Plan(site, scope, reason="设置里关了「重新生成时续接之前的网页会话」")
        try:
            row = self.get(site, scope)
        except Exception:
            log.exception("读网页会话记录失败")
            row = None
        if row is None or not row.get("url"):
            return Plan(site, scope, reason="这个作用域在这个网站上还没有网页会话")
        if int(row.get("turns") or 0) >= self.max_turns:
            return Plan(site, scope, reason=f"上一条会话已经发过 {row['turns']} 轮（上限 {self.max_turns}），新开一条")
        return Plan(site, scope, url=row["url"], turns=int(row.get("turns") or 0))

    # ------------------------------------------------------------ 记
    def record(self, plan: Plan, spec: Any, url: str | None, *, continued: bool | None = None,
               note: str = "") -> dict:
        """网页调用结束后记下对话地址。continued：页面流程报的「真的续接上了」（None = 按 plan 算）；
        note：页面流程退回新对话的原因。返回给日志和调用明细用的 {mode, url, reason, site, scope, turns}。"""
        cont = plan.continuing if continued is None else bool(continued and plan.continuing)
        reason = note or plan.reason
        cid = conversation_id(spec, url)
        if cont and cid and cid != conversation_id(spec, plan.url):
            cont, reason = False, f"续接后对话地址变了（{plan.url} → {url}），按新会话记"
        info = {"mode": "continue" if cont else "new", "url": url if cid else (plan.url if cont else None),
                "reason": None if cont else (reason or None), "site": plan.site,
                "scope": plan.scope.key if plan.scope else None, "turns": (plan.turns + 1) if cont else 1}
        if plan.scope is None:
            return info
        if not cid:
            info["note"] = f"没从标签页地址（{url or '读不到'}）认出对话，这次没有记下会话"
            return info
        scope, site = plan.scope, plan.site

        def write() -> None:
            from backend.domain.models import BrowserConversation as BC, utcnow
            try:
                with self.db.session() as s:
                    r = s.scalar(select(BC).where(BC.site == site, BC.scope_kind == scope.kind,
                                                  BC.scope_id == scope.id))
                    now = utcnow()
                    if r is None:
                        r = BC(site=site, scope_kind=scope.kind, scope_id=scope.id, created_at=now)
                        s.add(r)
                    if cont and r.conversation_id == cid:
                        r.turns = int(r.turns or 0) + 1
                    else:
                        r.turns, r.created_at = 1, now
                    r.url, r.conversation_id, r.updated_at = url, cid, now
                    if scope.project_id is not None:
                        r.project_id = scope.project_id
            except Exception:
                log.exception("记网页会话失败（%s %s）", site, scope.key)

        try:
            self.db.after_session(write)
        except Exception:
            log.exception("记网页会话失败（%s %s）", site, scope.key)
        return info


def describe(info: dict, label: str) -> str:
    """任务日志里的一句：续接会话 <url>（第 N 轮）/ 新开会话 <url>（原因）。"""
    url = info.get("url") or "（没读到地址）"
    if info.get("mode") == "continue":
        text = f"{label}：续接会话 {url}（这条会话第 {info.get('turns')} 轮）"
    else:
        why = f"（{info['reason']}）" if info.get("reason") else ""
        text = f"{label}：新开会话 {url}{why}"
    if info.get("note"):
        text += f"；{info['note']}"
    return text
