"""首帧阶段：每个任务给一个镜头出一张首帧候选。"""
from __future__ import annotations

import shutil

from backend.domain.refs import shot_refs
from backend.domain.models import Shot
from backend.domain.slots import add_candidate, get_or_create_slot, selected_candidate
from backend.engines.base import ImageRequest
from backend.pipeline.common import load_episode
from backend.pipeline.prompting import (compose_image_prompt, join_negative, keyframe_input_hash,
                                        shot_seed)
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
    BrowserRunner。两者都把产物下载到任务目录，返回文件列表和任务目录。"""
    sv = ctx.services
    browser = getattr(call, "runner", "comfyui") == "browser"
    if browser and getattr(sv, "browser", None) is None:
        raise JobError("网页出图的浏览器没有配置")
    if not browser and sv.comfy is None:
        raise JobError("ComfyUI 未配置")
    labels = ref_labels or {}
    for ref in call.dropped_refs:
        ctx.log(f"超出引擎参考图上限，已丢弃：{labels.get(ref, ref)}")
    run_dir = sv.media.run_dir(slug, ctx.job_id)
    if browser:
        files = sv.browser.run(call, run_dir=run_dir, cancel=ctx.cancel_event,
                               on_progress=ctx.progress, log=ctx.log)
    else:
        files = sv.comfy.run(call, run_dir=run_dir, cancel=ctx.cancel_event,
                             on_progress=ctx.progress)
    return files, run_dir


def ref_labels(packed, paths) -> dict[str, str]:
    """引擎丢弃参考图时报的是路径，换成"陈师傅 三视图·背面"这样的名字记日志。"""
    return {str(p): r.label for r, p in zip(packed, paths)}


@HANDLERS.register("image.keyframe", gpu=True)
def run_keyframe(ctx, payload: dict) -> dict:
    sv = ctx.services
    shot_id = int(payload["shot_id"])
    with sv.db.session() as s:
        shot, ep, proj, style = load_shot(s, shot_id)
        engine_name = shot.image_engine or proj.image_engine
        try:
            engine = sv.engines.image(engine_name)
        except KeyError as e:
            raise JobError(str(e)) from None
        anchor = style.anchor_text if style else ""
        prompt = compose_image_prompt(shot.image_prompt, anchor)
        negative = join_negative(proj.negative, style.negative if style else "")
        packed = shot_refs(s, shot)
        refs = [sv.media.abs(r.file_path) for r in packed]
        labels = ref_labels(packed, refs)
        h = keyframe_input_hash(shot, proj, style, engine_name,
                                [r.candidate_id for r in packed[:engine.caps.max_refs]])
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
    files, run_dir = run_comfy(ctx, call, slug, labels)
    dst = sv.media.new_media_path(slug, "first_frame", files[0].suffix)
    shutil.copyfile(files[0], dst)
    with sv.db.session() as s:
        slot = get_or_create_slot(s, "shot", shot_id, "first_frame")
        c = add_candidate(s, slot, file_path=sv.media.rel(dst), engine=engine_name,
                          params={**call.meta, "workflow": sv.media.rel(run_dir / "workflow.api.json")},
                          input_hash=h, job_id=ctx.job_id)
        cid = c.id
    return {"candidate_ids": [cid], "file": sv.media.rel(dst)}
