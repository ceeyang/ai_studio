"""触发阶段：把"跑某个阶段"拆成任务。"""
from __future__ import annotations

from fastapi import APIRouter, Request

from backend.app.deps import get_or_404, manager, services
from backend.app.schemas import FitKhmerIn, RunIn, RunOut, TranslateIn
from backend.domain.models import Episode
from backend.pipeline.runs import FIT_STAGE, STAGES, RunOptions, submit_chain

router = APIRouter(prefix="/api", tags=["runs"])


@router.post("/episodes/{episode_id}/run", response_model=RunOut)
def run_stage(episode_id: int, body: RunIn, request: Request):
    sv = services(request)
    stages = list(STAGES) if body.stage == "all" else [body.stage, *(body.then or [])]
    opts = RunOptions(shots=body.shots, candidates=body.candidates, force=body.force,
                      priority=body.priority,
                      export=body.export.model_dump() if body.export else None)
    return RunOut(job_ids=submit_chain(manager(request), sv.db, sv.engines, episode_id, stages, opts))


@router.post("/episodes/{episode_id}/dialogue/fit-khmer", response_model=RunOut)
def fit_khmer(episode_id: int, body: FitKhmerIn, request: Request):
    """按高棉语配音时长改写中文台词（dialogue.fit_khmer），可接着重出视频、导出。"""
    sv = services(request)
    opts = RunOptions(shots=[str(i) for i in body.shot_ids] if body.shot_ids else None,
                      force=body.force,
                      export=body.export.model_dump() if body.export else None)
    return RunOut(job_ids=submit_chain(manager(request), sv.db, sv.engines, episode_id,
                                       [FIT_STAGE, *body.then], opts))


@router.post("/episodes/{episode_id}/subtitles/translate", response_model=RunOut)
def translate_subtitles(episode_id: int, body: TranslateIn, request: Request):
    """给台词出高棉语 AI 译稿（只补缺的，force 全部重翻）。结果是草稿，需要母语者校对。"""
    sv = services(request)
    with sv.db.session() as s:
        ep = get_or_404(s, Episode, episode_id, "剧集")
        project_id = ep.project_id
    payload: dict = {"episode_id": episode_id, "force": body.force}
    if body.glossary:
        payload["glossary"] = body.glossary
    jid = manager(request).submit("subtitles.translate", payload, owner_type="episode",
                                  owner_id=episode_id, project_id=project_id)
    return RunOut(job_ids=[jid])
