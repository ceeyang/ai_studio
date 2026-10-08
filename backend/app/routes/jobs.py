"""任务与队列。"""
from __future__ import annotations

from fastapi import APIRouter, Request
from sqlalchemy import select

from backend.app.deps import manager, project_by_slug, services
from backend.app.schemas import InputIn, RetryIn

router = APIRouter(prefix="/api", tags=["jobs"])


@router.get("/jobs")
def list_jobs(request: Request, status: str | None = None, project: str | None = None,
              limit: int = 100):
    project_id = None
    if project:
        with services(request).db.session() as s:
            project_id = project_by_slug(s, project).id
    statuses = [x.strip() for x in status.split(",") if x.strip()] if status else None
    return manager(request).list_jobs(statuses=statuses, project_id=project_id, limit=limit)


@router.get("/jobs/{job_id}")
def get_job(job_id: int, request: Request):
    return manager(request).get(job_id)


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: int, request: Request):
    return {"ok": manager(request).cancel(job_id)}


@router.post("/jobs/{job_id}/retry")
def retry_job(job_id: int, body: RetryIn, request: Request):
    return {"job_id": manager(request).retry(job_id, body.payload)}


@router.post("/jobs/{job_id}/input")
def provide_input(job_id: int, body: InputIn, request: Request):
    return manager(request).provide_input(job_id, body.text)


@router.get("/queue")
def queue_state(request: Request):
    return manager(request).queue_state()


@router.post("/queue/pause")
def pause(request: Request):
    manager(request).pause()
    return manager(request).queue_state()


@router.post("/queue/resume")
def resume(request: Request):
    manager(request).resume()
    return manager(request).queue_state()
