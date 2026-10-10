"""任务与队列。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from backend.app.deps import manager, project_by_slug, services
from backend.app.schemas import InputIn, JobOut, RetryIn
from backend.app.views import with_context

router = APIRouter(prefix="/api", tags=["jobs"])


def _mem_snapshot(client) -> dict:
    return {"vram_free_gb": client.vram_free_gb(), "ram_free_gb": client.ram_free_gb(),
            "process_rss_gb": client.process_rss_gb()}


@router.get("/jobs", response_model=list[JobOut])
def list_jobs(request: Request, status: str | None = None, project: str | None = None,
              limit: int = 100):
    project_id = None
    db = services(request).db
    if project:
        with db.session() as s:
            project_id = project_by_slug(s, project).id
    statuses = [x.strip() for x in status.split(",") if x.strip()] if status else None
    jobs = manager(request).list_jobs(statuses=statuses, project_id=project_id, limit=limit)
    with db.session() as s:
        return with_context(s, jobs)


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: int, request: Request):
    job = manager(request).get(job_id)
    with services(request).db.session() as s:
        return with_context(s, [job])[0]


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


@router.post("/queue/free-gpu")
def free_gpu(request: Request):
    """手动释放 ComfyUI 的显存/内存（自动释放之外，想立刻腾出来就点这个）。"""
    m = manager(request)
    if m.gpu_running():
        raise HTTPException(409, "有任务正在用显卡，等它结束再释放")
    sv = services(request)
    before = _mem_snapshot(sv.comfy_client)
    sv.comfy.free()
    m.current_family = None
    after = _mem_snapshot(sv.comfy_client)
    return {"before": before, "after": after}
