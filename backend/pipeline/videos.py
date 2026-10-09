"""视频阶段：用选定的首帧逐镜出视频。"""
from __future__ import annotations

import shutil

from backend.domain.characters import cast_portraits
from backend.domain.slots import add_candidate, get_or_create_slot, selected_candidate
from backend.engines.base import VideoRequest
from backend.pipeline.keyframes import load_shot, run_comfy
from backend.pipeline.prompting import (compose_video_prompt, join_negative, shot_seed,
                                        video_input_hash)
from backend.queue.errors import JobError
from backend.queue.handlers import HANDLERS

VIDEO_EXTS = (".mp4", ".webm", ".mov", ".mkv")


@HANDLERS.register("video.shot", gpu=True)
def run_video(ctx, payload: dict) -> dict:
    sv = ctx.services
    shot_id = int(payload["shot_id"])
    with sv.db.session() as s:
        shot, ep, proj, style = load_shot(s, shot_id)
        ff = selected_candidate(s, "shot", shot.id, "first_frame")
        if ff is None:
            raise JobError(f"镜头 {shot.code} 还没有选定首帧，先跑 keyframes")
        engine_name = shot.video_engine or proj.video_engine
        try:
            engine = sv.engines.video(engine_name)
        except KeyError as e:
            raise JobError(str(e)) from None
        has_audio = engine.caps.has_audio
        prompt = compose_video_prompt(shot.motion_prompt, shot.image_prompt, shot.audio_prompt,
                                      shot.dialogue, anchor=style.anchor_text if style else "",
                                      has_audio=has_audio)
        negative = join_negative(proj.negative, style.negative if style else "")
        portraits = cast_portraits(s, shot)
        refs = [sv.media.abs(c.file_path) for c in portraits]
        h = video_input_hash(shot, proj, style, ff.id, engine_name, has_audio,
                             [c.id for c in portraits[:engine.caps.max_refs]])
        if not payload.get("force"):
            cur = selected_candidate(s, "shot", shot.id, "video")
            if cur is not None and cur.input_hash == h:  # hash 里含首帧候选 id
                ctx.log(f"视频 {shot.code}：输入没变，跳过（要重出用 --force）")
                return {"skipped": True, "candidate_ids": []}
        slot = get_or_create_slot(s, "shot", shot.id, "video")
        seed = shot_seed(proj.seed, shot.id, len(slot.candidates))
        seconds = max(1.0, min(float(shot.duration), engine.caps.max_seconds))
        first = sv.media.abs(ff.file_path)
        slug, code, aspect = proj.slug, shot.code, proj.aspect
    call = engine.build(VideoRequest(prompt=prompt, first_frame=first, aspect=aspect, seed=seed,
                                     seconds=seconds, negative=negative, refs=refs,
                                     prefix=f"studio/{slug}/clip_{code}"))
    ctx.log(f"视频 {code} · {engine_name} · {seconds:.1f}s · seed {seed} · 参考图 {len(refs)} 张"
            f"{'（原生有声）' if has_audio else '（无声）'}")
    files, run_dir = run_comfy(ctx, call, slug)
    video = next((f for f in files if f.suffix.lower() in VIDEO_EXTS), None)
    if video is None:
        raise JobError(f"镜头 {code} 没有产出视频文件：{[f.name for f in files]}")
    dst = sv.media.new_media_path(slug, "video", video.suffix)
    shutil.copyfile(video, dst)
    with sv.db.session() as s:
        slot = get_or_create_slot(s, "shot", shot_id, "video")
        c = add_candidate(s, slot, file_path=sv.media.rel(dst), engine=engine_name,
                          params={**call.meta, "first_frame_candidate_id": ff.id,
                                  "workflow": sv.media.rel(run_dir / "workflow.api.json")},
                          input_hash=h, job_id=ctx.job_id)
        cid = c.id
    return {"candidate_ids": [cid], "file": sv.media.rel(dst)}
