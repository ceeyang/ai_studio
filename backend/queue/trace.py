"""任务的调用明细（trace）：每次 LLM 调用、出图 / 视频引擎提交、ffmpeg 命令各记一条，存进 job_traces。

- 队列执行任务时（JobManager._execute）给当前线程装一个 JobTracer，处理器里任何地方调
  trace_add / trace_update 都记到这个任务下；不在任务里（API 直接调 LLM、测试）时是空操作。
- 先 add（发请求前，带上提示词、参数），出结果后 update（回复、耗时、产物），任务跑着时就能看到发了什么。
- 写库照 append_log 的做法：处理器常在自己没提交的写事务里调用，这时另开连接写库会被自己的写锁卡到
  busy_timeout 报 database is locked，所以用 db.after_session 等这个线程的会话结束后再写。
- 敏感信息：键名像 api_key / authorization 的值、已配置的 API Key 原文、像 sk-… / AIza… 的串一律换成掩码
  （复用 API 服务的 mask）。请求头不记。
- 单个字段超过 FIELD_LIMIT 字符：全文落盘到 <data_dir>/jobs/<job_id>/，记录里只留开头一段并注明文件名。
- 记录失败只写服务日志，绝不影响任务本身。
"""
from __future__ import annotations

import contextvars
import json
import logging
import re
import threading
from collections.abc import Iterable
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

log = logging.getLogger(__name__)

FIELD_LIMIT = 200_000
KINDS = ("llm", "engine", "ffmpeg", "note")
TRACE_DIR = "jobs"

_current: contextvars.ContextVar[JobTracer | None] = contextvars.ContextVar("job_tracer", default=None)

SECRET_FIELD = re.compile(r"^(api[_-]?key|apikey|x-api-key|x-goog-api-key|authorization|"
                          r"proxy-authorization|secret|client[_-]?secret|password|passwd|"
                          r"access[_-]?token|refresh[_-]?token|bearer|cookie|set-cookie|headers)$", re.I)
SECRET_VALUE = re.compile(r"(sk-[A-Za-z0-9_\-]{16,}|AIza[0-9A-Za-z_\-]{30,}|"
                          r"Bearer\s+[A-Za-z0-9._\-]{16,})")


def _mask(key: str) -> str:
    from backend.domain.api_keys import mask

    return mask(key) or "…"


def _scrub_text(text: str, secrets: Iterable[str]) -> str:
    out = text
    for k in secrets:
        if k and len(k) >= 6 and k in out:
            out = out.replace(k, _mask(k))
    return SECRET_VALUE.sub(lambda m: (m.group(0).split()[0] + " " + _mask(m.group(0).split()[-1])
                                       if m.group(0).lower().startswith("bearer")
                                       else _mask(m.group(0))), out)


def sanitize(value: Any, secrets: Iterable[str] = ()) -> Any:
    """递归把敏感信息换成掩码；值转成能存 JSON 的东西（Path → 字符串，其他对象 → str）。"""
    secrets = tuple(s for s in secrets if s)
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            k = str(k)
            if SECRET_FIELD.match(k) and v not in (None, "", {}, []):
                out[k] = _mask(v) if isinstance(v, str) else "（已隐藏）"
            else:
                out[k] = sanitize(v, secrets)
        return out
    if isinstance(value, (list, tuple)):
        return [sanitize(v, secrets) for v in value]
    if isinstance(value, str):
        return _scrub_text(value, secrets)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, Path):
        return _scrub_text(str(value), secrets)
    return _scrub_text(str(value), secrets)


def collect_secrets(services: Any) -> list[str]:
    """现在配着的 API Key 原文（API 服务、RunningHub）：出现在记录里的一律掩码。"""
    out: list[str] = []
    if services is None:
        return out
    keys = getattr(services, "api_keys", None)
    if keys is not None:
        try:
            from backend.domain.api_keys import SERVICES
            for sid in SERVICES:
                k = keys.resolve(sid).api_key
                if k:
                    out.append(k)
        except Exception as e:  # 读不到 Key 不影响任务
            log.debug("读 API Key 失败：%s", e)
    try:
        rh = getattr(getattr(services, "cfg", None), "runninghub", None)
        k = rh.api_key() if rh is not None else None
        if k:
            out.append(k)
    except Exception:
        pass
    return out


class JobTracer:
    """一个任务一次执行的记录器。seq 接着库里已有的往后排（等人工输入后重新执行会接着记）。"""

    def __init__(self, db: Any, job_id: int, *, data_dir: Path | None = None,
                 secrets: Iterable[str] = (), bus: Any = None):
        self.db = db
        self.job_id = job_id
        self.data_dir = Path(data_dir).resolve() if data_dir else None
        self.secrets = tuple(s for s in secrets if s)
        self.bus = bus
        self._lock = threading.Lock()
        self._rows: dict[int, dict] = {}
        self._last: dict[str, int] = {}
        self._seq = self._max_seq()
        self.added = 0          # 这次执行记了几条

    def _max_seq(self) -> int:
        from backend.domain.models import JobTrace
        try:
            with self.db.session() as s:
                return int(s.scalar(select(func.max(JobTrace.seq))
                                    .where(JobTrace.job_id == self.job_id)) or 0)
        except Exception:
            return 0

    # -------------------------------------------------------------- 路径
    def rel(self, path: Any) -> str:
        """本机文件路径 → 相对 data_dir 的 posix 路径（不在 data_dir 下的原样返回）。"""
        if path is None:
            return ""
        p = Path(str(path))
        if self.data_dir is not None:
            try:
                return p.resolve().relative_to(self.data_dir).as_posix()
            except (ValueError, OSError):
                pass
        return str(path)

    # -------------------------------------------------------------- 记录
    def add(self, kind: str, title: str, data: dict | None = None) -> int:
        with self._lock:
            self._seq += 1
            self.added += 1
            seq = self._seq
            self._rows[seq] = {"kind": kind, "title": title[:200], "data": dict(data or {})}
            self._last[kind] = seq
        self._write(seq)
        return seq

    def update(self, seq: int | None, patch: dict | None = None, *, title: str | None = None) -> None:
        if seq is None:
            return
        with self._lock:
            row = self._rows.get(seq)
            if row is None:
                return
            row["data"].update(patch or {})
            if title is not None:
                row["title"] = title[:200]
        self._write(seq)

    def last(self, kind: str) -> int | None:
        return self._last.get(kind)

    def _fit(self, seq: int, data: dict) -> dict:
        """超过 FIELD_LIMIT 的字段落盘，记录里只留开头。"""
        out = dict(data)
        files = dict(out.get("_files") or {})
        for k, v in list(out.items()):
            if k == "_files":
                continue
            text = v if isinstance(v, str) else None
            if text is None and isinstance(v, (dict, list)):
                try:
                    dumped = json.dumps(v, ensure_ascii=False, indent=2)
                except (TypeError, ValueError):
                    dumped = str(v)
                if len(dumped) <= FIELD_LIMIT:
                    continue
                text = dumped
            if text is None or len(text) <= FIELD_LIMIT:
                continue
            name = f"trace-{seq:03d}-{re.sub(r'[^A-Za-z0-9_.-]', '_', k)}.txt"
            saved = ""
            if self.data_dir is not None:
                try:
                    d = self.data_dir / TRACE_DIR / str(self.job_id)
                    d.mkdir(parents=True, exist_ok=True)
                    (d / name).write_text(text, encoding="utf-8")
                    saved = name
                except OSError as e:
                    log.warning("调用明细落盘失败：%s", e)
            where = f"全文存在 {TRACE_DIR}/{self.job_id}/{name}" if saved else "全文没能落盘"
            out[k] = text[:FIELD_LIMIT] + f"\n…（已截断：全文 {len(text)} 字，{where}）"
            files[k] = {"file": saved, "size": len(text)}
        if files:
            out["_files"] = files
        return out

    def _write(self, seq: int) -> None:
        with self._lock:
            row = self._rows.get(seq)
            if row is None:
                return
            kind, title = row["kind"], row["title"]
            try:
                data = self._fit(seq, sanitize(row["data"], self.secrets))
                title = sanitize(title, self.secrets)
            except Exception:
                log.exception("调用明细整理失败（任务 %s）", self.job_id)
                return

        def write() -> None:
            from backend.domain.models import JobTrace
            try:
                with self.db.session() as s:
                    r = s.scalar(select(JobTrace).where(JobTrace.job_id == self.job_id,
                                                        JobTrace.seq == seq))
                    if r is None:
                        s.add(JobTrace(job_id=self.job_id, seq=seq, kind=kind, title=title, data=data))
                    else:
                        r.title, r.data = title, data
            except Exception:
                log.exception("调用明细写库失败（任务 %s #%s）", self.job_id, seq)

        try:
            self.db.after_session(write)
        except Exception:
            log.exception("调用明细写库失败（任务 %s #%s）", self.job_id, seq)
        if self.bus is not None:
            try:
                self.bus.publish({"type": "trace", "job_id": self.job_id, "seq": seq})
            except Exception:
                pass


# ------------------------------------------------------------------ 模块级入口
def current() -> JobTracer | None:
    return _current.get()


@contextmanager
def use_tracer(tracer: JobTracer | None):
    token = _current.set(tracer)
    try:
        yield tracer
    finally:
        _current.reset(token)


def trace_add(kind: str, title: str, data: dict | None = None) -> int | None:
    t = _current.get()
    if t is None:
        return None
    try:
        return t.add(kind, title, data)
    except Exception:
        log.exception("记录调用明细失败")
        return None


def trace_update(seq: int | None, patch: dict | None = None, *, title: str | None = None) -> None:
    t = _current.get()
    if t is None or seq is None:
        return
    try:
        t.update(seq, patch, title=title)
    except Exception:
        log.exception("更新调用明细失败")


def trace_rel(path: Any) -> str:
    t = _current.get()
    return t.rel(path) if t is not None else str(path)


def annotate_last(kind: str, patch: dict) -> None:
    """给这个任务最近一条某类记录补字段（比如引擎调用产出的候选 id）。"""
    t = _current.get()
    if t is None:
        return
    seq = t.last(kind)
    if seq is None:
        return
    try:
        row = t._rows.get(seq) or {}
        data = row.get("data") or {}
        merged = {}
        for k, v in patch.items():
            old = data.get(k)
            if isinstance(old, list) and isinstance(v, list):
                merged[k] = old + [x for x in v if x not in old]
            else:
                merged[k] = v
        t.update(seq, merged)
    except Exception:
        log.exception("补充调用明细失败")


# ------------------------------------------------------------------ 读
def list_traces(db: Any, job_id: int) -> list[dict]:
    from backend.domain.models import JobTrace
    with db.session() as s:
        rows = list(s.scalars(select(JobTrace).where(JobTrace.job_id == job_id)
                              .order_by(JobTrace.seq)))
        return [{"id": r.id, "seq": r.seq, "kind": r.kind, "title": r.title,
                 "data": sanitize(r.data or {}),
                 "created_at": r.created_at.isoformat() + "Z" if r.created_at else None}
                for r in rows]
