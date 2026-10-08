"""各阶段共用的小工具。"""
from __future__ import annotations

from sqlalchemy.orm import Session

from backend.domain.models import Episode, Project
from backend.queue.errors import JobError


def load_episode(s: Session, episode_id: int) -> tuple[Episode, Project]:
    ep = s.get(Episode, episode_id)
    if ep is None:
        raise JobError(f"找不到剧集 {episode_id}")
    return ep, ep.project


def continue_chain(ctx, episode_id: int, payload: dict) -> list[int]:
    """payload 带 then（后续阶段）时，本阶段完成后接着提交。"""
    then = payload.get("then") or []
    if not then:
        return []
    from backend.pipeline.runs import RunOptions, submit_chain

    opts = RunOptions(**(payload.get("options") or {}))
    sv = ctx.services
    ids = submit_chain(ctx.manager, sv.db, sv.engines, episode_id, then, opts)
    ctx.log(f"已提交后续任务：{', '.join(map(str, ids))}")
    return ids
