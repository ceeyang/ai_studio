"""合成导出：按镜头顺序把选定视频拼成成片。"""
from __future__ import annotations

import shutil
from datetime import datetime

from backend.domain.models import Episode, Export
from backend.domain.slots import selected_candidate
from backend.pipeline.common import load_episode
from backend.queue.errors import JobError
from backend.queue.handlers import HANDLERS

OUTPUT_SIZES = {"9:16": (1080, 1920), "16:9": (1920, 1080)}
OUTPUT_FPS = 24


@HANDLERS.register("export.episode")
def run_export(ctx, payload: dict) -> dict:
    sv = ctx.services
    episode_id = int(payload["episode_id"])
    with sv.db.session() as s:
        ep, proj = load_episode(s, episode_id)
        if not ep.shots:
            raise JobError("这一集还没有镜头")
        clips, transitions, missing = [], [], []
        for sh in ep.shots:
            c = selected_candidate(s, "shot", sh.id, "video")
            if c is None:
                missing.append(sh.code)
                continue
            clips.append(sv.media.abs(c.file_path))
            transitions.append(sh.transition_out)
        slug, number, aspect = proj.slug, ep.number, proj.aspect
    if missing and not payload.get("allow_missing"):
        raise JobError(f"以下镜头还没有选定视频：{', '.join(missing)}")
    if not clips:
        raise JobError("没有可合成的视频")
    if aspect not in OUTPUT_SIZES:
        raise JobError(f"不支持的画幅 {aspect}")
    width, height = OUTPUT_SIZES[aspect]
    variant = aspect.replace(":", "x")
    out = sv.media.export_path(slug, f"ep{number:02d}_{variant}_{datetime.now():%Y%m%d_%H%M%S}.mp4")
    work = sv.media.run_dir(slug, ctx.job_id) / "work"
    ctx.log(f"合成 {len(clips)} 个镜头 → {width}x{height}@{OUTPUT_FPS}")
    sv.ffmpeg.assemble(clips, out, width=width, height=height, fps=OUTPUT_FPS,
                       transitions=transitions[:-1], work_dir=work)
    shutil.rmtree(work, ignore_errors=True)
    rel = sv.media.rel(out)
    with sv.db.session() as s:
        e = Export(episode_id=episode_id, variant=variant, file_path=rel,
                   settings={"width": width, "height": height, "fps": OUTPUT_FPS,
                             "clips": len(clips), "missing": missing})
        s.add(e)
        s.get(Episode, episode_id).status = "exported"
        s.flush()
        export_id = e.id
    return {"export_id": export_id, "file": rel}
