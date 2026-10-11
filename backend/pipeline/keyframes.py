"""首帧阶段：每个任务给一个镜头出一张首帧候选。"""
from __future__ import annotations

import shutil

from backend.domain.refs import shot_refs, split_refs
from backend.domain.models import Shot
from backend.domain.slots import add_candidate, get_or_create_slot, selected_candidate
from backend.engines.base import ImageRequest
from backend.engines.runninghub.client import RunningHubError
from backend.pipeline.common import load_episode
from backend.pipeline.prompting import (engine_image_prompt, join_negative, keyframe_input_hash,
                                        keyframe_inputs, shot_image_prompt, shot_seed)
from backend.queue.errors import JobError
from backend.queue.handlers import HANDLERS


def load_shot(s, shot_id: int):
    shot = s.get(Shot, shot_id)
    if shot is None:
        raise JobError(f"找不到镜头 {shot_id}")
    ep, proj = load_episode(s, shot.episode_id)
    return shot, ep, proj, proj.style_bible


def run_comfy(ctx, call, slug: str, ref_labels: dict[str, str] | None = None):
    """执行引擎调用：ComfyUI 工作流交给 ComfyRunner，网页出图（call.runner == "browser"）交给
    BrowserRunner，RunningHub 云端（call.runner == "runninghub"）交给 RunningHubRunner。
    都把产物下载到任务目录，返回文件列表和任务目录。"""
    sv = ctx.services
    kind = getattr(call, "runner", "comfyui")
    browser = kind == "browser"
    if browser and getattr(sv, "browser", None) is None:
        raise JobError("网页出图的浏览器没有配置")
    if kind == "runninghub" and getattr(sv, "runninghub", None) is None:
        raise JobError("RunningHub 没有配置（config.yaml 的 runninghub）")
    if kind == "comfyui" and sv.comfy is None:
        raise JobError("ComfyUI 未配置")
    labels = ref_labels or {}
    for ref in call.dropped_refs:
        ctx.log(f"超出引擎参考图上限，已丢弃：{labels.get(ref, ref)}")
    run_dir = sv.media.run_dir(slug, ctx.job_id)
    if kind == "runninghub":
        try:
            files = sv.runninghub.run(call, run_dir=run_dir, cancel=ctx.cancel_event,
                                      on_progress=ctx.progress, log=ctx.log)
        except RunningHubError as e:
            raise JobError(str(e)) from None
    elif browser:
        files = sv.browser.run(call, run_dir=run_dir, cancel=ctx.cancel_event,
                               on_progress=ctx.progress, log=ctx.log)
    else:
        files = sv.comfy.run(call, run_dir=run_dir, cancel=ctx.cancel_event,
                             on_progress=ctx.progress)
    return files, run_dir


def missing_portraits(s, shot) -> list[str]:
    """这一镜出场、但还没有选定定妆照的角色名。"""
    from backend.domain.characters import portrait_of
    from backend.domain.models import Character

    out = []
    for c in shot.cast or []:
        cid = c.get("character_id") if isinstance(c, dict) else None
        ch = s.get(Character, cid) if cid is not None else None
        if ch is not None and not ch.voice_only and ch.name not in out                 and portrait_of(s, ch.id) is None:
            out.append(ch.name)
    return out


def ref_labels(packed, paths) -> dict[str, str]:
    """引擎丢弃参考图时报的是路径，换成"陈师傅 三视图·背面"这样的名字记日志。"""
    return {str(p): r.label for r, p in zip(packed, paths)}


@HANDLERS.register("image.keyframe", gpu=True)
def run_keyframe(ctx, payload: dict) -> dict:
    sv = ctx.services
    shot_id = int(payload["shot_id"])
    with sv.db.session() as s:
        shot, ep, proj, style = load_shot(s, shot_id)
        continuing = bool(shot.continue_from_prev)
    if continuing:   # 接上一镜：不出图，首帧取上一镜视频的最后一帧
        from backend.pipeline.continuity import run_continue_frame
        return run_continue_frame(ctx, payload)
    with sv.db.session() as s:
        shot, ep, proj, style = load_shot(s, shot_id)
        engine_name = shot.image_engine or proj.image_engine
        try:
            engine = sv.engines.image(engine_name)
        except KeyError as e:
            raise JobError(str(e)) from None
        anchor = style.anchor_text if style else ""
        notes: list[str] = []
        # 发给引擎：景别那一句放在风格锚前面（只改发出去的版本，输入哈希仍按锚在前算）
        prompt = engine_image_prompt(shot_image_prompt(shot, proj, notes), anchor)
        negative = join_negative(proj.negative, style.negative if style else "")
        packed = shot_refs(s, shot)
        no_portrait = missing_portraits(s, shot) if engine.caps.max_refs > 0 else []
        # 超出上限时先保住每个角色的定妆照（split_refs），只把留下的发给引擎；引擎不吃参考图时照发，
        # 由下面的日志说明用不上
        used, dropped = (split_refs(packed, engine.caps.max_refs) if engine.caps.max_refs > 0
                         else (packed, []))
        refs = [sv.media.abs(r.file_path) for r in used]
        labels = ref_labels(used, refs)
        if engine.caps.max_refs == 0:
            used = []
        h = keyframe_input_hash(shot, proj, style, engine_name, [r.candidate_id for r in used])
        inputs = keyframe_inputs(shot, proj, style, engine_name, used)
        # 只要一个候选、又不强制时，输入没变就不重出（多候选是用户明确要抽卡）
        if not payload.get("force") and int(payload.get("candidates") or 1) == 1:
            cur = selected_candidate(s, "shot", shot.id, "first_frame")
            if cur is not None and cur.input_hash == h:
                ctx.log(f"首帧 {shot.code}：输入没变，跳过（要重出用 --force）")
                return {"skipped": True, "candidate_ids": []}
        slot = get_or_create_slot(s, "shot", shot.id, "first_frame")
        seed = shot_seed(proj.seed, shot.id, len(slot.candidates))
        slug, code, aspect = proj.slug, shot.code, proj.aspect
    if not shot.image_prompt.strip():
        raise JobError(f"镜头 {code} 没有 image_prompt")
    call = engine.build(ImageRequest(prompt=prompt, negative=negative, aspect=aspect, seed=seed,
                                     refs=refs, prefix=f"studio/{slug}/kf_{code}"))
    seed_text = "无种子（网页出图每次不同）" if call.runner == "browser" else f"seed {seed}"
    ctx.log(f"首帧 {code} · {engine_name} · {seed_text} · 参考图 {len(refs)} 张"
            f"{'：' + '、'.join(labels.values()) if labels else ''}")
    for n in notes:
        ctx.log(f"首帧 {code}：{n}")
    for r in dropped:
        ctx.log(f"超出引擎参考图上限，已丢弃：{r.label}")
    lost = [r.label for r in dropped if r.kind == "portrait"]
    if lost:
        ctx.log(f"首帧 {code}：{engine_name} 最多 {engine.caps.max_refs} 张参考图，装不下所有出场角色的"
                f"定妆照，{'、'.join(lost)}没带上，这几个人的脸只能按文字出；要锁脸就减少出场角色或换"
                "参考图上限更大的引擎")
    if no_portrait:
        ctx.log(f"首帧 {code}：{'、'.join(no_portrait)} 还没有定妆照，{engine_name} 只能按文字出脸；"
                "补了定妆照之后这张首帧会过期重出")
    if refs and engine.caps.max_refs == 0:
        ctx.log(f"首帧 {code}：{engine_name} 不吃参考图，{'、'.join(labels.values())}用不上，"
                f"人物一致只靠提示词里的外貌文字；要锁脸可换 H3 图像")
    files, run_dir = run_comfy(ctx, call, slug, labels)
    dst = sv.media.new_media_path(slug, "first_frame", files[0].suffix)
    shutil.copyfile(files[0], dst)
    with sv.db.session() as s:
        slot = get_or_create_slot(s, "shot", shot_id, "first_frame")
        c = add_candidate(s, slot, file_path=sv.media.rel(dst), engine=engine_name,
                          params={**call.meta, "workflow": sv.media.rel(run_dir / "workflow.api.json"),
                                  "inputs": inputs},
                          input_hash=h, job_id=ctx.job_id,
                          # 重拍一张：新的一条直接选中；一次出多张就不抢选定（不然最后完成的那张随机胜出），
                          # 让用户看「N 张新候选」自己挑
                          force_select=bool(payload.get("force"))
                          and int(payload.get("candidates") or 1) == 1)
        cid = c.id
    return {"candidate_ids": [cid], "file": sv.media.rel(dst)}
