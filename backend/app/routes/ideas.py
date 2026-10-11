"""从一个点子出片（首页向导，docs/specs/2026-10-11-idea-to-video-design.md）：出方向、扩写梗概、开始制作、继续出片。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from backend.app.deps import manager, services
from backend.app.routes.projects import create_project, make_slug
from backend.app.routes.runs import _precheck
from backend.app.schemas import (AutopilotOut, IdeaExpandIn, IdeaExpandOut, IdeaOptionIO, IdeaOptionsIn,
                                 IdeaOptionsOut, IdeaStartIn, IdeaStartOut, ProjectIn)
from backend.domain.models import Episode, Project
from backend.pipeline.episode_idea import IdeaFailed
from backend.pipeline.ideas import IdeaOption, expand_idea, idea_options, submit_autopilot
from backend.pipeline.runs import RunOptions, portrait_warnings, submit_chain

router = APIRouter(prefix="/api", tags=["ideas"])


@router.post("/ideas/options", response_model=IdeaOptionsOut)
def options(body: IdeaOptionsIn, request: Request):
    """一句话点子 → 几个差异明显的方向。什么都不存。"""
    try:
        got = idea_options(services(request), body.idea, target_seconds=body.target_seconds,
                           style_preset=body.style_preset, feedback=body.feedback, exclude=body.exclude,
                           count=body.count)
    except IdeaFailed as e:
        raise HTTPException(502, str(e)) from None
    return IdeaOptionsOut(options=[IdeaOptionIO(**o.model_dump()) for o in got])


@router.post("/ideas/expand", response_model=IdeaExpandOut)
def expand(body: IdeaExpandIn, request: Request):
    """选中的方向 → 片名 + 梗概；带 current + instruction 时按修改要求改写。什么都不存。"""
    try:
        out = expand_idea(services(request), body.idea, [IdeaOption(**o.model_dump()) for o in body.options],
                          target_seconds=body.target_seconds, style_preset=body.style_preset,
                          current=body.current, instruction=body.instruction)
    except IdeaFailed as e:
        raise HTTPException(502, str(e)) from None
    return IdeaExpandOut(title=out.title, synopsis=out.synopsis)


def unique_slug(sv, title: str) -> str:
    """片名转 slug；已经有这个项目就加 -2、-3……"""
    base = make_slug(title)[:60] or "project"
    with sv.db.session() as s:
        taken = set(s.scalars(select(Project.slug).where(Project.slug.like(f"{base}%"))))
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


@router.post("/ideas/start", status_code=201, response_model=IdeaStartOut)
def start(body: IdeaStartIn, request: Request):
    """开始制作：建项目（片名当项目名）+ 第 1 集（原文 = 梗概），排剧本 → 分镜；分镜做完在工作台点「继续出片」。"""
    sv = services(request)
    proj = create_project(ProjectIn(title=body.title.strip(), slug=unique_slug(sv, body.title), aspect=body.aspect,
                                    style_preset=body.style_preset, image_engine=body.image_engine,
                                    video_engine=body.video_engine), request)
    with sv.db.session() as s:
        ep = Episode(project_id=proj.id, number=1, title=body.title.strip(),
                     source_text=body.synopsis.strip(), target_seconds=body.target_seconds)
        s.add(ep)
        s.flush()
        ep_id = ep.id
    ids = submit_chain(manager(request), sv.db, sv.engines, ep_id, ["script", "storyboard"], RunOptions())
    return IdeaStartOut(slug=proj.slug, episode_id=ep_id, episode_number=1, job_ids=ids)


@router.post("/episodes/{episode_id}/autopilot", response_model=AutopilotOut)
def autopilot(episode_id: int, request: Request):
    """继续出片：出镜角色缺的定妆照 → 首帧 → 视频 → 导出，一次排好（用任务依赖串起来）。"""
    sv = services(request)
    ids, skipped = submit_autopilot(sv, manager(request), episode_id, precheck=_precheck(sv))
    # 有角色没能出定妆照（没有外貌提示词）：照常给「首帧只能按文字出脸」的提醒
    warnings = portrait_warnings(sv.db, sv.engines, episode_id, ["keyframes"]) if skipped else []
    return AutopilotOut(job_ids=ids, skipped=skipped, warnings=warnings)
