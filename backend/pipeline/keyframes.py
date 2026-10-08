"""首帧阶段：每个任务给一个镜头出一张首帧候选。"""
from __future__ import annotations

import shutil

from backend.domain.models import Shot
from backend.domain.slots import add_candidate, get_or_create_slot
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


def run_comfy(ctx, call, slug: str):
    sv = ctx.services
    if sv.comfy is None:
        raise JobError("ComfyUI 未配置")
    for ref in call.dropped_refs:
        ctx.log(f"超出引擎参考图上限，已丢弃：{ref}")
    run_dir = sv.media.run_dir(slug, ctx.job_id)
    files = sv.comfy.run(call, run_dir=run_dir, cancel=ctx.cancel_event, on_progress=ctx.progress)
    return files, run_dir


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
        h = keyframe_input_hash(shot, proj, style, engine_name)
        slot = get_or_create_slot(s, "shot", shot.id, "first_frame")
        seed = shot_seed(proj.seed, shot.id, len(slot.candidates))
        slug, code, aspect = proj.slug, shot.code, proj.aspect
    if not shot.image_prompt.strip():
        raise JobError(f"镜头 {code} 没有 image_prompt")
    call = engine.build(ImageRequest(prompt=prompt, negative=negative, aspect=aspect, seed=seed,
                                     prefix=f"studio/{slug}/kf_{code}"))
    ctx.log(f"首帧 {code} · {engine_name} · seed {seed}")
    files, run_dir = run_comfy(ctx, call, slug)
    dst = sv.media.new_media_path(slug, "first_frame", files[0].suffix)
    shutil.copyfile(files[0], dst)
    with sv.db.session() as s:
        slot = get_or_create_slot(s, "shot", shot_id, "first_frame")
        c = add_candidate(s, slot, file_path=sv.media.rel(dst), engine=engine_name,
                          params={**call.meta, "workflow": sv.media.rel(run_dir / "workflow.api.json")},
                          input_hash=h, job_id=ctx.job_id)
        cid = c.id
    return {"candidate_ids": [cid], "file": sv.media.rel(dst)}
