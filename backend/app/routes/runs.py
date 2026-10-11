"""触发阶段：把"跑某个阶段"拆成任务。"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query, Request

from backend.app.deps import get_or_404, manager, services
from backend.app.schemas import FitKhmerIn, RerunImpact, RunIn, RunOut, TranslateIn
from backend.domain.models import Episode
from backend.engines.comfyui import health
from backend.pipeline.impact import protected_losses, rerun_impact
from backend.pipeline.runs import (FIT_STAGE, STAGES, Precheck, RunOptions, continue_wait_notes,
                                   portrait_warnings, submit_chain)
from backend.queue.errors import JobError

router = APIRouter(prefix="/api", tags=["runs"])


def _precheck(sv) -> Precheck:
    """用户点「跑」时提交前校验：缺首帧 / 缺视频 / ComfyUI 连不上都马上报错，不排进队列再失败。"""
    def comfy():
        h = health.check_comfy(sv.comfy_client)
        return h.ok, health.comfy_url(sv.comfy_client)
    return Precheck(comfy=comfy)


@router.get("/episodes/{episode_id}/rerun-impact", response_model=RerunImpact)
def get_rerun_impact(episode_id: int, stage: Literal["script", "storyboard", "all"],
                     request: Request,
                     force: bool = False, scene_block_ids: str | None = None,
                     note: str | None = Query(None, max_length=500)):
    """现在跑这个阶段会不会动、会删什么——给前端跑之前弹确认用，不调用 LLM。

    scene_block_ids：逗号分隔的段落 id，只计这些段（只对 storyboard 有效）；note 有内容时按 force 算。
    """
    sv = services(request)
    block_ids = _parse_ids(scene_block_ids)
    with sv.db.session() as s:
        get_or_404(s, Episode, episode_id, "剧集")
        return rerun_impact(s, sv, episode_id, stage, force, scene_block_ids=block_ids, note=note)


def _parse_ids(raw: str | None) -> list[int] | None:
    toks = [t.strip() for t in (raw or "").split(",") if t.strip()]
    try:
        return [int(t) for t in toks] or None
    except ValueError:
        raise JobError(f"段落 id 格式不对：{raw}（应为逗号分隔的数字）") from None


@router.post("/episodes/{episode_id}/run", response_model=RunOut)
def run_stage(episode_id: int, body: RunIn, request: Request):
    sv = services(request)
    stages = list(STAGES) if body.stage == "all" else [body.stage, *(body.then or [])]
    if not body.confirm:
        # 不强制的剧本/分镜运行会删掉做过的镜头（有选定首帧/视频、改过）：先拒绝，确认后再带 confirm=true
        with sv.db.session() as s:
            loss = (protected_losses(s, sv, episode_id, stages, force=body.force,
                                     scene_block_ids=body.scene_block_ids, note=body.note)
                    if s.get(Episode, episode_id) is not None else None)
        if loss:
            raise JobError(loss)
    opts = RunOptions(shots=body.shots, candidates=body.candidates, force=body.force,
                      priority=body.priority,
                      export=body.export.model_dump() if body.export else None,
                      scene_block_ids=body.scene_block_ids, note=body.note,
                      new_conversation=body.new_conversation)
    notes = continue_wait_notes(sv.db, episode_id, stages, body.shots)   # 提交前算：跳过的是哪些
    ids = submit_chain(manager(request), sv.db, sv.engines, episode_id, stages, opts,
                       precheck=_precheck(sv))
    return RunOut(job_ids=ids, warnings=portrait_warnings(sv.db, sv.engines, episode_id, stages,
                                                          body.shots), notes=notes)


@router.post("/episodes/{episode_id}/dialogue/fit-khmer", response_model=RunOut)
def fit_khmer(episode_id: int, body: FitKhmerIn, request: Request):
    """按高棉语配音时长改写中文台词（dialogue.fit_khmer），可接着重出视频、导出。"""
    sv = services(request)
    opts = RunOptions(shots=[str(i) for i in body.shot_ids] if body.shot_ids else None,
                      force=body.force,
                      export=body.export.model_dump() if body.export else None)
    return RunOut(job_ids=submit_chain(manager(request), sv.db, sv.engines, episode_id,
                                       [FIT_STAGE, *body.then], opts, precheck=_precheck(sv)))


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
