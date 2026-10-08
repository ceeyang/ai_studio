"""触发阶段：把"跑某个阶段"拆成任务。"""
from __future__ import annotations

from fastapi import APIRouter, Request

from backend.app.deps import manager, services
from backend.app.schemas import RunIn, RunOut
from backend.pipeline.runs import STAGES, RunOptions, submit_chain

router = APIRouter(prefix="/api", tags=["runs"])


@router.post("/episodes/{episode_id}/run", response_model=RunOut)
def run_stage(episode_id: int, body: RunIn, request: Request):
    sv = services(request)
    stages = list(STAGES) if body.stage == "all" else [body.stage]
    opts = RunOptions(shots=body.shots, candidates=body.candidates, force=body.force,
                      priority=body.priority)
    return RunOut(job_ids=submit_chain(manager(request), sv.db, sv.engines, episode_id, stages, opts))
