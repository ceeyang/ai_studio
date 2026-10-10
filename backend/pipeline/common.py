"""各阶段共用的小工具。"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.domain.hashing import input_hash
from backend.domain.models import Episode, Project, SceneBlock
from backend.queue.errors import JobError


def load_episode(s: Session, episode_id: int) -> tuple[Episode, Project]:
    ep = s.get(Episode, episode_id)
    if ep is None:
        raise JobError(f"找不到剧集 {episode_id}")
    return ep, ep.project


def clean_note(note: str | None) -> str | None:
    """修改意见：去首尾空白，空串当作没有。"""
    return (note or "").strip() or None


def prompt_fp(kind: str, system: str, user: str, note: str | None = None) -> str:
    """渲染后提示词的输入指纹。没有修改意见时和以前完全一样（老数据不会被当成过期）；
    有修改意见时单独记进指纹，项目自定义模板没写 note 变量也能区分出来。"""
    return input_hash(kind, system, user, note=note) if note else input_hash(kind, system, user)


def check_scene_blocks(s: Session, episode_id: int, stage: str, block_ids: list[int]) -> None:
    """按段重写的校验：只有分镜能按段重写，段落必须是这一集的。"""
    if stage != "storyboard":
        raise JobError("只有分镜能按段重写")
    own = set(s.scalars(select(SceneBlock.id).where(SceneBlock.episode_id == episode_id)))
    bad = [str(i) for i in block_ids if i not in own]
    if bad:
        raise JobError(f"段落 {'、'.join(bad)} 不属于这一集")


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
