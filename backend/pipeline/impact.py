"""重跑影响预估：现在跑 script / storyboard 会不会动、会删什么——不调用 LLM。

和 pipeline.script / pipeline.storyboard 共用同一套指纹判断（structure_prompt_fp /
visual_prompt_fp / storyboard_prompt_fp），避免「要不要跳过」的逻辑在两处走偏。
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.schemas import RerunImpact
from backend.domain.models import Shot, Slot
from backend.pipeline.common import load_episode
from backend.pipeline.script import structure_prompt_fp, visual_prompt_fp
from backend.pipeline.storyboard import allocate_shots, shot_budget, storyboard_prompt_fp
from backend.queue.errors import JobError


def _manual_kept(s: Session, episode_id: int) -> int:
    return s.scalar(select(func.count()).select_from(Shot)
                    .where(Shot.episode_id == episode_id, Shot.origin == "manual")) or 0


def _selected_counts(s: Session, shot_ids: list[int]) -> tuple[int, int]:
    """这些镜头里，有选定首帧 / 选定视频的各有多少个。"""
    if not shot_ids:
        return 0, 0
    first = video = 0
    slots = s.scalars(select(Slot).where(Slot.owner_type == "shot", Slot.owner_id.in_(shot_ids),
                                         Slot.selected_candidate_id.is_not(None)))
    for sl in slots:
        if sl.kind == "first_frame":
            first += 1
        elif sl.kind == "video":
            video += 1
    return first, video


def _last_action(s: Session, block_id: int) -> str:
    sh = s.scalar(select(Shot).where(Shot.scene_block_id == block_id)
                  .order_by(Shot.order.desc(), Shot.id.desc()))
    return sh.action if sh else ""


def _storyboard_impact(s: Session, sv, episode_id: int, force: bool) -> RerunImpact:
    ep, proj = load_episode(s, episode_id)
    blocks = list(ep.scene_blocks)
    if not blocks:
        raise JobError("这一集还没有剧本段落，先跑 script")
    try:
        caps = sv.engines.video(proj.video_engine).caps
    except KeyError as e:
        raise JobError(str(e)) from None
    shot_seconds, max_seconds = caps.default_seconds, caps.max_seconds
    counts = allocate_shots(shot_budget(ep.target_seconds, shot_seconds),
                            [len(b.text) for b in blocks])
    anchor = proj.style_bible.anchor_text if proj.style_bible else ""
    characters = [{"name": c.name, "visual_prompt": c.visual_prompt or c.description}
                 for c in proj.characters]
    locs = {l.id: {"name": l.name, "time_of_day": l.time_of_day, "atmosphere": l.atmosphere,
                   "visual_prompt": l.visual_prompt} for l in proj.locations}
    prompts = sv.prompts(proj.slug)
    stage_hashes = ep.stage_hashes or {}

    rerun_block_ids: list[int] = []
    rerun_block_numbers: list[int] = []
    prev_action = ""
    for b, n in zip(blocks, counts):
        _, _, fp = storyboard_prompt_fp(prompts, anchor, characters, locs.get(b.location_id),
                                        b.text, n, shot_seconds, max_seconds, prev_action)
        key = f"storyboard:{b.id}"
        existing = s.scalar(select(func.count()).select_from(Shot)
                            .where(Shot.scene_block_id == b.id))
        unchanged = stage_hashes.get(key) == fp and existing > 0
        if force or not unchanged:
            rerun_block_ids.append(b.id)
            rerun_block_numbers.append(b.order + 1)
        # 这一段不管重不重跑，下一段的"上一镜结束时"都看数据库里现在的样子——
        # 真正重跑时如果上一段也变了，实际衔接语句会不一样，这里只是预估，接受这点误差。
        prev_action = _last_action(s, b.id) or prev_action

    will_run = bool(rerun_block_numbers)
    shot_ids: list[int] = []
    if will_run:
        # 整段重跑时，会先删掉这几段自己的 llm 镜头，跑完后再把剩下没挂段落的 llm 孤儿也清掉
        # （对应 run_storyboard 里 done 且 not only 时的孤儿清理；预估按不勾段落的整集重跑算）
        cond = (Shot.episode_id == episode_id, Shot.origin == "llm",
               (Shot.scene_block_id.in_(rerun_block_ids) | Shot.scene_block_id.is_(None)))
        shot_ids = list(s.scalars(select(Shot.id).where(*cond)))
    first_sel, video_sel = _selected_counts(s, shot_ids)
    return RerunImpact(stage="storyboard", will_run=will_run, blocks=rerun_block_numbers,
                       shots_deleted=len(shot_ids), selected_first_frames=first_sel,
                       selected_videos=video_sel, manual_kept=_manual_kept(s, episode_id),
                       overwrites=[])


def _script_impact(s: Session, sv, episode_id: int, force: bool) -> RerunImpact:
    ep, proj = load_episode(s, episode_id)
    prompts = sv.prompts(proj.slug)
    stage_hashes = ep.stage_hashes or {}

    _, _, structure_fp = structure_prompt_fp(prompts, proj.title, ep.target_seconds,
                                             ep.source_text)
    structure_unchanged = stage_hashes.get("structure") == structure_fp and bool(ep.scene_blocks)
    structure_will_run = force or not structure_unchanged

    chars = [{"name": c.name, "description": c.description} for c in proj.characters]
    locations = [{"name": l.name, "time_of_day": l.time_of_day, "atmosphere": l.atmosphere}
                for l in proj.locations]
    _, _, visual_fp = visual_prompt_fp(prompts, proj.title, chars, locations,
                                       ep.source_text[:4000])
    visual_unchanged = stage_hashes.get("visual") == visual_fp and bool(proj.style_bible_id)
    visual_will_run = force or not visual_unchanged

    shot_ids: list[int] = []
    if structure_will_run:
        # 段落会被清空，现有的 llm 镜头都会变成孤儿；之后跑分镜会把它们当孤儿删掉
        shot_ids = list(s.scalars(select(Shot.id).where(Shot.episode_id == episode_id,
                                                        Shot.origin == "llm")))
    first_sel, video_sel = _selected_counts(s, shot_ids)

    overwrites: list[str] = []
    if structure_will_run:
        overwrites += ["角色描述", "场景氛围"]
    if visual_will_run:
        overwrites += ["角色外观", "场景外观", "风格设定"]

    return RerunImpact(stage="script", will_run=structure_will_run or visual_will_run, blocks=[],
                       shots_deleted=len(shot_ids), selected_first_frames=first_sel,
                       selected_videos=video_sel, manual_kept=_manual_kept(s, episode_id),
                       overwrites=overwrites)


def rerun_impact(s: Session, sv, episode_id: int, stage: str, force: bool) -> RerunImpact:
    if stage == "storyboard":
        return _storyboard_impact(s, sv, episode_id, force)
    if stage == "script":
        return _script_impact(s, sv, episode_id, force)
    raise JobError(f"未知阶段：{stage}，可选：script、storyboard")
