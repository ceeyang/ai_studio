"""场景：改设定、场景图（AI 出图 / 上传 / 选定）。场景图是首帧参考图的第一张，换了这个场景的镜头首帧会过期。"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Request
from sqlalchemy import select

from backend.app.deps import check_engines, get_or_404, manager, project_by_slug, read_upload, services
from backend.app.schemas import LocationDrawIn, LocationOut, LocationPatch, RunOut
from backend.app.views import candidate_out, job_brief, owner_jobs
from backend.domain.locations import (LOCATION_SLOT, location_image_of, location_image_stale,
                                      set_location_image)
from backend.domain.models import Candidate, Job, Location, Slot
from backend.domain.settings import location_engine
from backend.domain.slots import select_candidate
from backend.engines.registry import check_ready
from backend.pipeline.locations import TASK, location_prompt
from backend.queue.manager import ACTIVE

router = APIRouter(prefix="/api", tags=["locations"])


def location_out(s, loc: Location, progress=None) -> LocationOut:
    sel = location_image_of(s, loc.id)
    slot = s.scalar(select(Slot).where(Slot.owner_type == "location", Slot.owner_id == loc.id,
                                       Slot.kind == LOCATION_SLOT))
    jobs = s.scalars(select(Job).where(Job.owner_type == "location", Job.owner_id == loc.id,
                                       Job.type == TASK, Job.status.in_(ACTIVE))
                     .order_by(Job.id)).all()
    failed = owner_jobs(s, "location", [loc.id], (TASK,))[loc.id].failure(TASK)
    if failed is not None and slot is not None and slot.candidates:
        newest = max(x.created_at for x in slot.candidates)
        if newest > (failed.finished_at or failed.created_at):
            failed = None
    return LocationOut.model_validate(loc).model_copy(update={
        "image_candidate_id": sel.id if sel else None,
        "image_url": "/media/" + quote(sel.file_path) if sel else None,
        "image_stale": location_image_stale(loc, sel),
        "images": [candidate_out(c) for c in slot.candidates] if slot else [],
        "active_jobs": [job_brief(j, progress) for j in jobs],
        "last_failure": job_brief(failed, failed=True) if failed else None})


@router.get("/projects/{slug}/locations", response_model=list[LocationOut])
def list_locations(slug: str, request: Request):
    with services(request).db.session() as s:
        return [location_out(s, x, manager(request).progress)
                for x in project_by_slug(s, slug).locations]


@router.patch("/locations/{loc_id}", response_model=LocationOut)
def patch_location(loc_id: int, body: LocationPatch, request: Request):
    with services(request).db.session() as s:
        loc = get_or_404(s, Location, loc_id, "场景")
        for k, v in body.model_dump(exclude_unset=True).items():
            setattr(loc, k, v)
        s.flush()
        return location_out(s, loc)


@router.post("/locations/{loc_id}/images/draw", response_model=RunOut)
def draw_location_images(loc_id: int, body: LocationDrawIn, request: Request):
    """出 count 张场景图候选（不出人物），每张一个任务。引擎：手选 → 设置页场景图这一步 → 项目的图像引擎。"""
    sv = services(request)
    with sv.db.session() as s:
        loc = get_or_404(s, Location, loc_id, "场景")
        location_prompt(loc, "")   # 没有英文画面提示词时直接报错，不排进队列再失败
        project_id = loc.project_id
        engine_name = location_engine(sv, loc.project.image_engine, body.engine)
    check_engines(sv, engine_name, None)
    eng = sv.engines.image(engine_name)
    check_ready(eng)
    m = manager(request)
    return RunOut(job_ids=[m.submit(TASK, {"location_id": loc_id, "engine": engine_name},
                                    project_id=project_id, owner_type="location", owner_id=loc_id,
                                    model_family=eng.family)
                           for _ in range(body.count)])


@router.post("/locations/{loc_id}/image", response_model=LocationOut)
async def upload_location_image(loc_id: int, request: Request):
    """上传场景图（multipart 的 file 字段），新图成为选定的场景图。"""
    data, name = await read_upload(request)
    sv = services(request)
    with sv.db.session() as s:
        loc = get_or_404(s, Location, loc_id, "场景")
        set_location_image(s, sv.media, loc, data, name)
        return location_out(s, loc)


@router.post("/locations/{loc_id}/images/{candidate_id}/use", response_model=LocationOut)
def use_location_image(loc_id: int, candidate_id: int, request: Request):
    """选定这张场景图。这个场景的镜头首帧按哈希规则变过期。"""
    with services(request).db.session() as s:
        loc = get_or_404(s, Location, loc_id, "场景")
        c = s.get(Candidate, candidate_id)
        slot = s.get(Slot, c.slot_id) if c is not None else None
        if slot is None or slot.owner_type != "location" or slot.owner_id != loc_id \
                or slot.kind != LOCATION_SLOT:
            raise LookupError(f"场景 {loc.name} 没有场景图候选 {candidate_id}")
        select_candidate(s, candidate_id)
        return location_out(s, loc)
