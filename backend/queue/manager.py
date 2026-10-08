"""任务队列：一个 GPU worker（串行）+ N 个 CPU worker，状态持久化在 jobs 表。

- GPU worker 持有 _gpu_lock 期间执行任务；gpu_exclusive() 抢同一把锁，
  用于本机 LLM（LM Studio）与 ComfyUI 互斥。
- 依赖：depends_on 里的任务全部 done 才可执行；任一 failed/canceled/不存在，
  依赖它的任务直接判失败，不会永远挂着。
"""
from __future__ import annotations

import logging
import threading
import time
import traceback
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from sqlalchemy import func, select

from backend.domain.db import Database
from backend.domain.models import Job, utcnow
from backend.queue.context import JobContext
from backend.queue.errors import JobCanceled, JobError, NeedsInput
from backend.queue.events import EventBus
from backend.queue.handlers import HandlerRegistry
from backend.queue.scheduler import QueuedJob, pick_next

log = logging.getLogger(__name__)

ACTIVE = ("queued", "running", "waiting_input")
DEAD = ("failed", "canceled", "missing")


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() + "Z" if dt else None


def job_to_dict(j: Job) -> dict:
    return {"id": j.id, "type": j.type, "status": j.status, "project_id": j.project_id,
            "owner_type": j.owner_type, "owner_id": j.owner_id, "payload": j.payload or {},
            "gpu": j.gpu, "model_family": j.model_family, "priority": j.priority,
            "depends_on": j.depends_on or [], "log": j.log or "", "error": j.error,
            "result": j.result, "created_at": _iso(j.created_at),
            "started_at": _iso(j.started_at), "finished_at": _iso(j.finished_at)}


class JobManager:
    def __init__(self, db: Database, registry: HandlerRegistry, bus: Any = None, *,
                 cpu_workers: int = 2, poll: float = 0.5):
        self.db = db
        self.registry = registry
        self.bus = bus or EventBus()
        self.services: Any = None
        self.on_family_switch: Callable[[str | None, str], None] | None = None
        self.on_gpu_handover: Callable[[], None] | None = None
        self.cpu_workers = cpu_workers
        self.poll = poll
        self.current_family: str | None = None
        self._gpu_lock = threading.Lock()
        self._claim_lock = threading.Lock()
        self._count_lock = threading.Lock()
        self._wake = threading.Condition()
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._llm_waiting = 0
        self._cancel: dict[int, threading.Event] = {}
        self._threads: list[threading.Thread] = []

    # ------------------------------------------------------------ 生命周期
    def recover(self) -> int:
        with self.db.session() as s:
            jobs = list(s.scalars(select(Job).where(Job.status == "running")))
            for j in jobs:
                j.status = "failed"
                j.error = "interrupted：服务重启时任务仍在运行，可一键重试"
                j.finished_at = utcnow()
        return len(jobs)

    def start(self) -> None:
        self._stop.clear()
        loops = [("gpu-worker", self._gpu_loop)] + [
            (f"cpu-worker-{i}", self._cpu_loop) for i in range(self.cpu_workers)]
        for name, fn in loops:
            t = threading.Thread(target=fn, name=name, daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        for ev in list(self._cancel.values()):
            ev.set()
        self._notify()
        for t in self._threads:
            t.join(timeout)
        self._threads.clear()

    # ------------------------------------------------------------ 提交与控制
    def submit(self, job_type: str, payload: dict | None = None, *, project_id: int | None = None,
               owner_type: str | None = None, owner_id: int | None = None, priority: int = 0,
               model_family: str | None = None, depends_on: list[int] | None = None) -> int:
        spec = self.registry.get(job_type)
        with self.db.session() as s:
            j = Job(type=job_type, payload=dict(payload or {}), gpu=spec.gpu,
                    project_id=project_id, owner_type=owner_type, owner_id=owner_id,
                    priority=priority, model_family=model_family,
                    depends_on=list(depends_on or []))
            s.add(j)
            s.flush()
            jid, d = j.id, job_to_dict(j)
        self._publish(d)
        self._notify()
        return jid

    def cancel(self, job_id: int) -> bool:
        d = None
        with self.db.session() as s:
            j = s.get(Job, job_id)
            if j is None:
                raise LookupError(f"找不到任务 {job_id}")
            if j.status in ("queued", "waiting_input"):
                j.status = "canceled"
                j.finished_at = utcnow()
                d = job_to_dict(j)
            elif j.status == "running":
                ev = self._cancel.get(job_id)
                if ev is None:
                    return False
                ev.set()
                return True
            else:
                return False
        self._publish(d)
        return True

    def retry(self, job_id: int, payload_override: dict | None = None) -> int:
        with self.db.session() as s:
            j = s.get(Job, job_id)
            if j is None:
                raise LookupError(f"找不到任务 {job_id}")
            if j.status not in ("failed", "canceled"):
                raise JobError(f"只有失败或取消的任务可以重试（当前 {j.status}）")
            payload = {**(j.payload or {}), **(payload_override or {})}
            meta = dict(project_id=j.project_id, owner_type=j.owner_type, owner_id=j.owner_id,
                        priority=j.priority, model_family=j.model_family)
            job_type = j.type
        return self.submit(job_type, payload, **meta)

    def provide_input(self, job_id: int, text: str) -> dict:
        with self.db.session() as s:
            j = s.get(Job, job_id)
            if j is None:
                raise LookupError(f"找不到任务 {job_id}")
            if j.status != "waiting_input":
                raise JobError(f"任务 {job_id} 不在等待输入状态（当前 {j.status}）")
            key = ((j.result or {}).get("input_request") or {}).get("key", "default")
            payload = dict(j.payload or {})
            manual = dict(payload.get("manual") or {})
            manual[key] = text
            payload["manual"] = manual
            j.payload = payload
            j.status = "queued"
            d = job_to_dict(j)
        self._publish(d)
        self._notify()
        return d

    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    def pause(self) -> None:
        self._paused.set()
        self.bus.publish({"type": "queue", **self.queue_state()})

    def resume(self) -> None:
        self._paused.clear()
        self._notify()
        self.bus.publish({"type": "queue", **self.queue_state()})

    def queue_state(self) -> dict:
        with self.db.session() as s:
            rows = s.execute(select(Job.status, func.count()).where(Job.status.in_(ACTIVE))
                             .group_by(Job.status)).all()
        counts = {st: 0 for st in ACTIVE}
        counts.update({st: n for st, n in rows})
        return {"paused": self.paused, "counts": counts, "current_family": self.current_family}

    def get(self, job_id: int) -> dict:
        with self.db.session() as s:
            j = s.get(Job, job_id)
            if j is None:
                raise LookupError(f"找不到任务 {job_id}")
            return job_to_dict(j)

    def list_jobs(self, statuses: list[str] | None = None, project_id: int | None = None,
                  limit: int = 100) -> list[dict]:
        q = select(Job).order_by(Job.id.desc()).limit(limit)
        if statuses:
            q = q.where(Job.status.in_(statuses))
        if project_id is not None:
            q = q.where(Job.project_id == project_id)
        with self.db.session() as s:
            return [job_to_dict(j) for j in s.scalars(q)]

    def append_log(self, job_id: int, msg: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {msg}\n"
        with self.db.session() as s:
            j = s.get(Job, job_id)
            if j is not None:
                j.log = (j.log or "") + line
        self.bus.publish({"type": "log", "job_id": job_id, "line": line})

    @contextmanager
    def gpu_exclusive(self) -> Iterator[None]:
        with self._count_lock:
            self._llm_waiting += 1
        try:
            self._gpu_lock.acquire()
        finally:
            with self._count_lock:
                self._llm_waiting -= 1
        try:
            if self.on_gpu_handover:
                try:
                    self.on_gpu_handover()
                except Exception as e:  # 释放显存失败不影响 LLM 调用
                    log.warning("让出显卡前释放显存失败：%s", e)
            self.current_family = None
            yield
        finally:
            self._gpu_lock.release()
            self._notify()

    # ------------------------------------------------------------ worker
    def _gpu_loop(self) -> None:
        while not self._stop.is_set():
            if self._paused.is_set() or self._llm_waiting:
                self._sleep()
                continue
            ran = False
            with self._gpu_lock:
                jid = self._claim(gpu=True)
                if jid is not None:
                    self._execute(jid)
                    ran = True
            if not ran:
                self._sleep()

    def _cpu_loop(self) -> None:
        while not self._stop.is_set():
            if self._paused.is_set():
                self._sleep()
                continue
            jid = self._claim(gpu=False)
            if jid is None:
                self._sleep()
                continue
            self._execute(jid)

    def _claim(self, *, gpu: bool) -> int | None:
        events: list[dict] = []
        picked: int | None = None
        with self._claim_lock, self.db.session() as s:
            rows = list(s.scalars(select(Job).where(Job.status == "queued", Job.gpu == gpu)))
            dep_ids = {d for j in rows for d in (j.depends_on or [])}
            statuses: dict[int, str] = {}
            if dep_ids:
                statuses = {i: st for i, st in s.execute(
                    select(Job.id, Job.status).where(Job.id.in_(dep_ids))).all()}
            ready = []
            for j in rows:
                deps = [statuses.get(d, "missing") for d in (j.depends_on or [])]
                if any(st in DEAD for st in deps):
                    j.status = "failed"
                    j.error = "上游任务失败、被取消或不存在，本任务未执行"
                    j.finished_at = utcnow()
                    events.append(job_to_dict(j))
                elif all(st == "done" for st in deps):
                    ready.append(QueuedJob(j.id, j.priority, j.model_family))
            choice = pick_next(ready, self.current_family if gpu else None)
            if choice is not None:
                j = s.get(Job, choice.id)
                j.status = "running"
                j.started_at = utcnow()
                self._cancel[j.id] = threading.Event()
                picked = j.id
                events.append(job_to_dict(j))
        for d in events:
            self._publish(d)
        return picked

    def _execute(self, jid: int) -> None:
        with self.db.session() as s:
            j = s.get(Job, jid)
            job_type, payload, family, is_gpu = j.type, dict(j.payload or {}), j.model_family, j.gpu
        cancel = self._cancel.setdefault(jid, threading.Event())
        status, error, result = "done", None, None
        try:
            spec = self.registry.get(job_type)
            if is_gpu and family:
                if self.current_family and family != self.current_family and self.on_family_switch:
                    try:
                        self.on_family_switch(self.current_family, family)
                    except Exception as e:
                        log.warning("换模型族前释放显存失败：%s", e)
                self.current_family = family
            if cancel.is_set():
                raise JobCanceled()
            result = spec.fn(JobContext(self, jid, cancel), payload)
        except JobCanceled:
            status = "canceled"
        except NeedsInput as e:
            status, result = "waiting_input", {"input_request": e.request}
        except JobError as e:
            status, error = "failed", str(e)
        except Exception as e:
            status, error = "failed", f"{type(e).__name__}: {e}"
            log.exception("任务 %s 失败", jid)
            self.append_log(jid, traceback.format_exc())
        finally:
            self._cancel.pop(jid, None)
        with self.db.session() as s:
            j = s.get(Job, jid)
            j.status = status
            j.error = error
            if result is not None:
                j.result = result
            if status != "waiting_input":
                j.finished_at = utcnow()
            d = job_to_dict(j)
        self._publish(d)
        self._notify()

    def _publish(self, d: dict | None) -> None:
        if d is not None:
            self.bus.publish({"type": "job", "job": d})

    def _sleep(self) -> None:
        with self._wake:
            self._wake.wait(self.poll)

    def _notify(self) -> None:
        with self._wake:
            self._wake.notify_all()
