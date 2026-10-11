"""视频阶段：用选定的首帧逐镜出视频。"""
from __future__ import annotations

import shutil

from backend.domain.refs import shot_refs
from backend.domain.slots import add_candidate, get_or_create_slot, selected_candidate
from backend.engines.base import VideoRequest
from backend.pipeline.common import job_services
from backend.pipeline.keyframes import load_shot, ref_labels, run_comfy
from backend.domain.models import Location, SceneBlock
from backend.pipeline.prompting import (compose_video_prompt, join_negative, location_summary,
                                        shot_seed, video_input_hash, video_inputs,
                                        with_continuity)
from backend.queue.errors import JobCanceled, JobError
from backend.queue.handlers import HANDLERS

VIDEO_EXTS = (".mp4", ".webm", ".mov", ".mkv")


def cut_note(cuts: list[float]) -> str:
    where = "、".join(f"{t:.1f}s" for t in cuts)
    return f"疑似跳切：{where} 处画面突变（可能剪进了参考图或换了场景），建议看一下，不对就重拍"


def scene_qc(ctx, sv, path, code: str) -> dict | None:
    """出片自检：找片段内部的硬切。检测本身失败只记日志，不影响出片。"""
    detect = getattr(sv.ffmpeg, "scene_cuts", None)
    if detect is None:
        return None
    try:
        cuts = detect(path)
    except JobCanceled:
        raise
    except Exception as e:
        why = (str(e).strip().splitlines() or [type(e).__name__])[0][:200]
        ctx.log(f"视频 {code}：跳切检测没跑成，跳过（{why}）")
        return None
    if cuts:
        ctx.log(f"视频 {code}：{cut_note(cuts)}")
    return {"hard_cuts": cuts}


def _shot_location(s, shot) -> str:
    """这一镜所在段落的场景外观摘要（英文）；手工镜头没挂段落、场景没写外观时为空。"""
    blk = s.get(SceneBlock, shot.scene_block_id) if shot.scene_block_id else None
    loc = s.get(Location, blk.location_id) if blk is not None and blk.location_id else None
    return location_summary(loc.visual_prompt) if loc is not None else ""


@HANDLERS.register("video.shot", gpu=True)
def run_video(ctx, payload: dict) -> dict:
    sv = job_services(ctx)
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
        packed = shot_refs(s, shot)
        refs = [sv.media.abs(r.file_path) for r in packed]
        labels = ref_labels(packed, refs)
        used = packed[:engine.caps.max_refs]
        h = video_input_hash(shot, proj, style, ff.id, engine_name, has_audio,
                             [r.candidate_id for r in used])
        inputs = video_inputs(shot, proj, style, ff.id, engine_name, has_audio, used)
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
        place = _shot_location(s, shot)
    # 连续性要求只加在发给引擎的提示词里，不进输入哈希
    call = engine.build(VideoRequest(prompt=with_continuity(prompt, place), first_frame=first,
                                     aspect=aspect, seed=seed,
                                     seconds=seconds, negative=negative, refs=refs,
                                     prefix=f"studio/{slug}/clip_{code}"))
    ctx.log(f"视频 {code} · {engine_name} · {seconds:.1f}s · seed {seed} · 参考图 {len(refs)} 张"
            f"{'（原生有声）' if has_audio else '（无声）'}"
            f"{'：' + '、'.join(labels.values()) if labels else ''}")
    files, run_dir = run_comfy(ctx, call, slug, labels)
    video = next((f for f in files if f.suffix.lower() in VIDEO_EXTS), None)
    if video is None:
        raise JobError(f"镜头 {code} 没有产出视频文件：{[f.name for f in files]}")
    dst = sv.media.new_media_path(slug, "video", video.suffix)
    shutil.copyfile(video, dst)
    canceled: JobCanceled | None = None
    try:
        qc = scene_qc(ctx, sv, dst, code)
    except JobCanceled as e:
        # 视频已经出好了：先登记成候选（没做自检），再把取消报上去，GPU 的活不白干
        qc, canceled = None, e
        ctx.log(f"视频 {code}：跳切检测时任务被取消，视频先登记成候选（没做跳切自检）")
    with sv.db.session() as s:
        slot = get_or_create_slot(s, "shot", shot_id, "video")
        params = {**call.meta, "first_frame_candidate_id": ff.id,
                  "workflow": sv.media.rel(run_dir / "workflow.api.json"), "inputs": inputs}
        if qc is not None:
            params["qc"] = qc
        c = add_candidate(s, slot, file_path=sv.media.rel(dst), engine=engine_name,
                          params=params,
                          input_hash=h, job_id=ctx.job_id,
                          force_select=bool(payload.get("force")))  # 重拍：新的一条直接选中
        if qc and qc["hard_cuts"]:
            c.note = cut_note(qc["hard_cuts"])
        cid = c.id
    if canceled is not None:
        raise canceled
    out = {"candidate_ids": [cid], "file": sv.media.rel(dst)}
    if qc is not None:
        out["qc"] = qc
    return out
