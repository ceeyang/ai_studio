"""各阶段共用的小工具。"""
from __future__ import annotations

from dataclasses import is_dataclass, replace

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.domain.hashing import input_hash
from backend.domain.models import Episode, Project, SceneBlock
from backend.queue.errors import JobError


def job_services(ctx):
    """本任务用的服务：ffmpeg 换成绑定了本任务取消信号的副本，取消时正在跑的 ffmpeg 会被杀掉。
    （测试里的假 ffmpeg 没有 with_cancel，原样返回。）"""
    sv = ctx.services
    ff = getattr(sv, "ffmpeg", None)
    if ff is None or not hasattr(ff, "with_cancel") or not is_dataclass(sv):
        return sv
    return replace(sv, ffmpeg=ff.with_cancel(ctx.cancel_event))


def load_episode(s: Session, episode_id: int) -> tuple[Episode, Project]:
    ep = s.get(Episode, episode_id)
    if ep is None:
        raise JobError(f"找不到剧集 {episode_id}")
    return ep, ep.project


def clean_note(note: str | None) -> str | None:
    """修改意见：去首尾空白，空串当作没有。"""
    return (note or "").strip() or None


def prompt_fp(kind: str, system: str, user: str) -> str:
    """渲染后提示词的输入指纹。

    调用方传的是不带修改意见（和上一版镜头）渲染出来的基础提示词：带意见的重出由 force 保证一定会跑，
    指纹只记输入本身——否则之后一次不强制的运行（整集运行、影响预估）会以为输入变了，
    把按意见改好的结果又按原样重做一遍。"""
    return input_hash(kind, system, user)


def template_rules(prompts, name: str, version: int) -> str:
    """这次用的模板规则：内置模板就是版本号；项目目录里有覆盖模板时再带上覆盖模板的内容哈希。

    指纹只算数据，模板文字的变化由这个版本号单独记：版本不同只提示「规则已更新」、不自动重跑——
    否则每改一次模板，所有剧集下一次整集运行都会重写剧本 / 分镜，删光镜头和选定的首帧、视频。"""
    source = getattr(prompts, "source", None)
    if source is None:
        return str(version)
    src, overridden = source(name)
    if not overridden:
        return str(version)
    return f"{version}+{input_hash(name + '-template', src)[:8]}"


def render_with_note(prompts, name: str, note: str | None, extra_note_vars: dict | None = None,
                     **variables) -> tuple[str, str, str, str]:
    """渲染提示词两遍：不带修改意见的基础版（算指纹用）和这次真正要发的版本（有意见时带上意见和 extra_note_vars）。
    返回 (system, user, base_system, base_user)。"""
    base_system, base_user = prompts.render(name, note=None, **variables)
    if not note:
        return base_system, base_user, base_system, base_user
    system, user = prompts.render(name, note=note, **{**variables, **(extra_note_vars or {})})
    return system, user, base_system, base_user


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
