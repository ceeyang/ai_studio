"""道具：分镜里出现过的道具（镜头 props 的英文说法）和道具图（AI 出图 / 上传 / 选定）。
道具图在首帧参考图里排在出场角色后面；换了选定的道具图，带这个道具的镜头首帧会过期。"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Request
from sqlalchemy import select

from backend.app.deps import check_engines, get_or_404, manager, project_by_slug, read_upload, services
from backend.app.schemas import LocationDrawIn, PropOut, PropPatch, PropUse, RunOut
from backend.app.views import candidate_out, job_brief, owner_jobs
from backend.domain.models import Candidate, Job, Project, Prop, Slot
from backend.domain.props import (PROP_SLOT, project_prop_uses, prop_image_of, prop_image_stale,
                                  prop_key, set_prop_image, sync_props)
from backend.domain.settings import prop_engine
from backend.domain.slots import select_candidate
from backend.engines.registry import check_ready
from backend.pipeline.props import TASK, prop_prompt
from backend.queue.manager import ACTIVE

router = APIRouter(prefix="/api", tags=["props"])


def prop_out(s, prop: Prop, progress=None, uses=None) -> PropOut:
    sel = prop_image_of(s, prop.id)
    slot = s.scalar(select(Slot).where(Slot.owner_type == "prop", Slot.owner_id == prop.id,
                                       Slot.kind == PROP_SLOT))
    jobs = s.scalars(select(Job).where(Job.owner_type == "prop", Job.owner_id == prop.id,
                                       Job.type == TASK, Job.status.in_(ACTIVE))
                     .order_by(Job.id)).all()
    failed = owner_jobs(s, "prop", [prop.id], (TASK,))[prop.id].failure(TASK)
    if failed is not None and slot is not None and slot.candidates:
        newest = max(x.created_at for x in slot.candidates)
        if newest > (failed.finished_at or failed.created_at):
            failed = None
    if uses is None:
        uses = project_prop_uses(s, prop.project_id).get(prop_key(prop.name), [])
    return PropOut.model_validate(prop).model_copy(update={
        "image_candidate_id": sel.id if sel else None,
        "image_url": "/media/" + quote(sel.file_path) if sel else None,
        "image_stale": prop_image_stale(prop, sel),
        "images": [candidate_out(c) for c in slot.candidates] if slot else [],
        "active_jobs": [job_brief(j, progress) for j in jobs],
        "last_failure": job_brief(failed, failed=True) if failed else None,
        "uses": [PropUse(episode_number=n, shot_code=code) for _, n, code in uses]})


@router.get("/projects/{slug}/props", response_model=list[PropOut])
def list_props(slug: str, request: Request):
    """本项目分镜里出现过的道具（按第一次出现的顺序）。第一次列出时给每个道具建一行。"""
    with services(request).db.session() as s:
        proj = project_by_slug(s, slug)
        progress = manager(request).progress
        return [prop_out(s, p, progress, uses) for p, uses in sync_props(s, proj)]


@router.patch("/props/{prop_id}", response_model=PropOut)
def patch_prop(prop_id: int, body: PropPatch, request: Request):
    with services(request).db.session() as s:
        p = get_or_404(s, Prop, prop_id, "道具")
        for k, v in body.model_dump(exclude_unset=True).items():
            if v is not None:
                setattr(p, k, v)
        s.flush()
        return prop_out(s, p)


@router.post("/props/{prop_id}/images/draw", response_model=RunOut)
def draw_prop_images(prop_id: int, body: LocationDrawIn, request: Request):
    """出 count 张道具图候选（只拍道具、白底），每张一个任务。引擎：手选 → 设置页道具图这一步 → 项目的图像引擎。"""
    sv = services(request)
    with sv.db.session() as s:
        p = get_or_404(s, Prop, prop_id, "道具")
        prop_prompt(p, "")   # 没有英文说法时直接报错，不排进队列再失败
        project_id = p.project_id
        engine_name = prop_engine(sv, s.get(Project, project_id).image_engine, body.engine)
    check_engines(sv, engine_name, None)
    eng = sv.engines.image(engine_name)
    check_ready(eng)
    m = manager(request)
    return RunOut(job_ids=[m.submit(TASK, {"prop_id": prop_id, "engine": engine_name},
                                    project_id=project_id, owner_type="prop", owner_id=prop_id,
                                    model_family=eng.family)
                           for _ in range(body.count)])


@router.post("/props/{prop_id}/image", response_model=PropOut)
async def upload_prop_image(prop_id: int, request: Request):
    """上传道具图（multipart 的 file 字段），新图成为选定的道具图。"""
    data, name = await read_upload(request)
    sv = services(request)
    with sv.db.session() as s:
        p = get_or_404(s, Prop, prop_id, "道具")
        set_prop_image(s, sv.media, p, s.get(Project, p.project_id).slug, data, name)
        return prop_out(s, p)


@router.post("/props/{prop_id}/images/{candidate_id}/use", response_model=PropOut)
def use_prop_image(prop_id: int, candidate_id: int, request: Request):
    """选定这张道具图。带这个道具的镜头首帧按哈希规则变过期。"""
    with services(request).db.session() as s:
        p = get_or_404(s, Prop, prop_id, "道具")
        c = s.get(Candidate, candidate_id)
        slot = s.get(Slot, c.slot_id) if c is not None else None
        if slot is None or slot.owner_type != "prop" or slot.owner_id != prop_id \
                or slot.kind != PROP_SLOT:
            raise LookupError(f"道具 {p.name} 没有道具图候选 {candidate_id}")
        select_candidate(s, candidate_id)
        return prop_out(s, p)
