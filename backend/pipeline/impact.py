"""重跑影响预估：现在跑 script / storyboard 会不会动、会删什么——不调用 LLM。

和 pipeline.script / pipeline.storyboard 共用同一套指纹判断（structure_prompt_fp /
visual_prompt_fp / storyboard_inputs_fp + judge_block），避免「要不要跳过」的逻辑在两处走偏。
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.schemas import RerunImpact
from backend.domain.models import Shot, Slot
from backend.pipeline.common import check_scene_blocks, clean_note, load_episode
from backend.pipeline.script import (structure_prompt_fp, visual_chars_and_locations,
                                     visual_prompt_fp)
from backend.pipeline.storyboard import (build_storyboard_plan, edited_shot_ids, judge_block,
                                         storyboard_inputs_fp, storyboard_rules)
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


@dataclass
class StoryboardScan:
    """不强制（或强制）跑分镜会动哪些段、删哪些镜头，其中哪些是生成后改过的。"""
    block_ids: list[int]
    block_numbers: list[int]
    shot_ids: list[int]
    edited_ids: list[int]
    rules_outdated: bool


def scan_storyboard(s: Session, sv, episode_id: int, force: bool,
                    only: list[int] | None = None) -> StoryboardScan:
    # 预算、风格锚、角色表、场景表都走和 run_storyboard 相同的 build_storyboard_plan，
    # 指纹判断走同一个 judge_block，避免两边对「要不要跳过」的判断走偏。
    plan_data = build_storyboard_plan(s, sv, episode_id)
    stage_hashes = plan_data.ep.stage_hashes or {}
    rules = storyboard_rules(plan_data.prompts)
    only_set = set(only or [])

    block_ids: list[int] = []
    numbers: list[int] = []
    edited: list[int] = []
    outdated = False
    prev_action = ""
    for blk in plan_data.blocks:
        if only_set and blk["id"] not in only_set:
            # 按段重写：其他段不动（和 run_storyboard 里跳过 only 之外的段一致）
            prev_action = _last_action(s, blk["id"]) or prev_action
            continue
        fp = storyboard_inputs_fp(plan_data.anchor, plan_data.characters, blk["location"],
                                  blk["text"], blk["count"], plan_data.shot_seconds,
                                  plan_data.max_seconds, prev_action, blk["seconds"])
        record = stage_hashes.get(f"storyboard:{blk['id']}")
        llm_shots = list(s.scalars(select(Shot).where(Shot.scene_block_id == blk["id"],
                                                      Shot.origin == "llm")))
        judged = judge_block(record, fp, rules, bool(llm_shots))
        outdated = outdated or judged.rules_outdated
        if force or not judged.unchanged:
            block_ids.append(blk["id"])
            numbers.append(blk["order"] + 1)
            edited += edited_shot_ids(record, llm_shots)
        # 这一段不管重不重跑，下一段的"上一镜结束时"都看数据库里现在的样子——
        # 真正重跑时如果上一段也变了，实际衔接语句会不一样，这里只是预估，接受这点误差。
        prev_action = _last_action(s, blk["id"]) or prev_action

    shot_ids: list[int] = []
    if numbers:
        # 重跑时会先删掉这几段自己的 llm 镜头；整集重跑（不按段）跑完后还会把没挂段落的 llm 孤儿也清掉
        # （对应 run_storyboard 里 done 且 not only 时的孤儿清理），按段重写不清孤儿
        in_blocks = Shot.scene_block_id.in_(block_ids)
        cond = (Shot.episode_id == episode_id, Shot.origin == "llm",
                in_blocks if only_set else (in_blocks | Shot.scene_block_id.is_(None)))
        shot_ids = list(s.scalars(select(Shot.id).where(*cond)))
    return StoryboardScan(block_ids, numbers, shot_ids, edited, outdated)


def _storyboard_impact(s: Session, sv, episode_id: int, force: bool,
                       only: list[int] | None = None) -> RerunImpact:
    scan = scan_storyboard(s, sv, episode_id, force, only)
    first_sel, video_sel = _selected_counts(s, scan.shot_ids)
    return RerunImpact(stage="storyboard", will_run=bool(scan.block_numbers),
                       blocks=scan.block_numbers, shots_deleted=len(scan.shot_ids),
                       selected_first_frames=first_sel, selected_videos=video_sel,
                       manual_kept=_manual_kept(s, episode_id), overwrites=[],
                       edited_shots=len(scan.edited_ids), rules_outdated=scan.rules_outdated)


def _episode_edited_ids(s: Session, ep) -> list[int]:
    """整集 llm 镜头里生成后改过的（按每段记下的镜头内容哈希比；旧记录不知道的不算）。"""
    stage_hashes = ep.stage_hashes or {}
    out: list[int] = []
    for b in ep.scene_blocks:
        shots = list(s.scalars(select(Shot).where(Shot.scene_block_id == b.id,
                                                  Shot.origin == "llm")))
        out += edited_shot_ids(stage_hashes.get(f"storyboard:{b.id}"), shots)
    return out


def _script_will_run(s: Session, sv, episode_id: int, force: bool) -> tuple[bool, bool]:
    """(结构会不会重跑, 视觉设定会不会重跑)。"""
    ep, proj = load_episode(s, episode_id)
    prompts = sv.prompts(proj.slug)
    stage_hashes = ep.stage_hashes or {}

    _, _, structure_fp = structure_prompt_fp(prompts, proj.title, ep.target_seconds,
                                             ep.source_text)
    structure_unchanged = stage_hashes.get("structure") == structure_fp and bool(ep.scene_blocks)
    structure_will_run = force or not structure_unchanged

    # 注意：这里的角色表/场景表是数据库里现在的样子。如果上面的结构阶段也会重跑，
    # 真正跑的时候结构会先把角色描述/场景氛围改掉，视觉阶段用的就是改过的新值——
    # 预估不调用 LLM，不知道新值会是什么，这里按"现在的值"估算视觉指纹，可能和真正重跑的结果不同。
    chars, locations = visual_chars_and_locations(proj)
    _, _, visual_fp = visual_prompt_fp(prompts, proj.title, chars, locations,
                                       ep.source_text[:4000])
    visual_unchanged = stage_hashes.get("visual") == visual_fp and bool(proj.style_bible_id)
    visual_will_run = force or not visual_unchanged
    return structure_will_run, visual_will_run


def _script_impact(s: Session, sv, episode_id: int, force: bool) -> RerunImpact:
    ep, _ = load_episode(s, episode_id)
    structure_will_run, visual_will_run = _script_will_run(s, sv, episode_id, force)

    shot_ids: list[int] = []
    edited: list[int] = []
    if structure_will_run:
        # 段落会被清空，现有的 llm 镜头都会变成孤儿；之后跑分镜会把它们当孤儿删掉
        shot_ids = list(s.scalars(select(Shot.id).where(Shot.episode_id == episode_id,
                                                        Shot.origin == "llm")))
        edited = _episode_edited_ids(s, ep)
    first_sel, video_sel = _selected_counts(s, shot_ids)

    overwrites: list[str] = []
    if structure_will_run:
        overwrites += ["角色描述", "场景氛围"]
    if visual_will_run:
        overwrites += ["角色外观", "场景外观", "风格设定"]

    return RerunImpact(stage="script", will_run=structure_will_run or visual_will_run, blocks=[],
                       shots_deleted=len(shot_ids), selected_first_frames=first_sel,
                       selected_videos=video_sel, manual_kept=_manual_kept(s, episode_id),
                       overwrites=overwrites, edited_shots=len(edited))


def rerun_impact(s: Session, sv, episode_id: int, stage: str, force: bool, *,
                 scene_block_ids: list[int] | None = None,
                 note: str | None = None) -> RerunImpact:
    """scene_block_ids：只重写这些段落的分镜（只对 storyboard 有效）；note 有内容时按 force 算。"""
    if clean_note(note):
        force = True
    if scene_block_ids:
        check_scene_blocks(s, episode_id, stage, scene_block_ids)
    if stage == "storyboard":
        return _storyboard_impact(s, sv, episode_id, force, scene_block_ids)
    if stage == "script":
        return _script_impact(s, sv, episode_id, force)
    raise JobError(f"未知阶段：{stage}，可选：script、storyboard")


def _selected_ids(s: Session, shot_ids: list[int]) -> set[int]:
    if not shot_ids:
        return set()
    return set(s.scalars(select(Slot.owner_id).where(
        Slot.owner_type == "shot", Slot.owner_id.in_(shot_ids),
        Slot.selected_candidate_id.is_not(None))))


def protected_losses(s: Session, sv, episode_id: int, stages: list[str], *, force: bool,
                     scene_block_ids: list[int] | None = None,
                     note: str | None = None) -> str | None:
    """不强制的剧本/分镜运行会删掉「做过的」镜头（有选定首帧/视频、生成后改过）时，返回一句说明；
    没有这类损失（或强制、带修改意见——用户明确要重出）返回 None。

    API 的 /run 和命令行靠它拦住：之前模板一改指纹全变，一次不强制的整集运行就会悄悄删光这些。"""
    if force or clean_note(note) or not stages or stages[0] not in ("script", "storyboard"):
        return None
    try:
        return _protected_losses(s, sv, episode_id, stages, scene_block_ids)
    except JobError:
        return None   # 还没有段落、引擎不存在之类：照常提交，由任务自己报错


def _protected_losses(s: Session, sv, episode_id: int, stages: list[str],
                      scene_block_ids: list[int] | None) -> str | None:
    ep, _ = load_episode(s, episode_id)
    shot_ids: list[int] = []
    edited: list[int] = []
    if stages[0] == "script":
        structure, visual = _script_will_run(s, sv, episode_id, False)
        # 结构重跑：段落清空，llm 镜头全成孤儿；视觉重跑又接着跑分镜：角色外观变了，每段都会重出
        if structure or (visual and "storyboard" in stages):
            shot_ids = list(s.scalars(select(Shot.id).where(Shot.episode_id == episode_id,
                                                            Shot.origin == "llm")))
            edited = _episode_edited_ids(s, ep)
        elif "storyboard" in stages:
            scan = scan_storyboard(s, sv, episode_id, False)
            shot_ids, edited = scan.shot_ids, scan.edited_ids
    else:
        scan = scan_storyboard(s, sv, episode_id, False, scene_block_ids)
        shot_ids, edited = scan.shot_ids, scan.edited_ids
    selected = _selected_ids(s, shot_ids)
    edited_set = set(edited) & set(shot_ids)
    if not selected and not edited_set:
        return None
    codes = {sid: code for sid, code in s.execute(select(Shot.id, Shot.code)
                                                  .where(Shot.id.in_(shot_ids)))}

    def names(ids) -> str:
        cs = sorted(codes.get(i) or f"#{i}" for i in ids)
        return "、".join(cs[:12]) + (f" 等 {len(cs)} 个" if len(cs) > 12 else "")

    parts = []
    if selected:
        parts.append(f"{names(selected)} 有选定的首帧或视频")
    if edited_set:
        parts.append(f"{names(edited_set)} 生成后改过")
    return (f"这次运行会删掉 {len(shot_ids)} 个镜头重新生成，其中{'；'.join(parts)}。"
            "确认要删就再提交一次并带 confirm=true（界面上点确认，命令行加 --yes）；"
            "不想动现有镜头就不要跑这一步")
