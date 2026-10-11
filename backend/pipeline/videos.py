"""视频阶段：用选定的首帧逐镜出视频。"""
from __future__ import annotations

import shutil

from backend.domain.refs import legacy_video_refs
from backend.domain.slots import add_candidate, get_or_create_slot, selected_candidate
from backend.engines.base import VideoRequest
from backend.pipeline.common import job_services
from backend.pipeline.continuity import video_ref_plan
from backend.pipeline.keyframes import load_shot, ref_labels, run_comfy
from backend.domain.models import Location, SceneBlock
from backend.pipeline.prompting import (STAY_IN_FRAME, Speaker, compose_video_prompt,
                                        entering_clause, gender_of, insert_kind, token_refs, join_negative, location_summary, short_ref,
                                        shot_image_prompt, shot_seed, video_input_hash,
                                        video_inputs, with_continuity)
from backend.queue.errors import JobCanceled, JobError
from backend.queue.handlers import HANDLERS

VIDEO_EXTS = (".mp4", ".webm", ".mov", ".mkv")


def cut_note(cuts: list[float]) -> str:
    where = "、".join(f"{t:.1f}s" for t in cuts)
    return f"疑似跳切：{where} 处画面突变（可能剪进了参考图或换了场景），建议看一下，不对就重拍"


# 视频第 0 帧和选定首帧的相似度（ffmpeg ssim，见 FFmpeg.first_frame_ssim）低于这个值：H3 没从首帧开始
START_SSIM_MIN = 0.6


def start_note(ssim: float) -> str:
    return (f"开头没接上首帧：视频第一帧和选定的首帧不像（相似度 {ssim:.2f}），H3 可能重新构图了，"
            "接上一镜、场景和人物位置都对不上，建议看一下，不对就重拍")


def qc_bad(qc: dict | None) -> bool:
    """出片自检有没有发现问题：疑似跳切，或开头没接上首帧。"""
    return bool(qc and (qc.get("hard_cuts") or qc.get("start_mismatch")))


def qc_note(qc: dict | None) -> str:
    if not qc:
        return ""
    parts = []
    if qc.get("start_mismatch"):
        parts.append(start_note(float(qc.get("first_frame_ssim") or 0)))
    if qc.get("hard_cuts"):
        parts.append(cut_note(qc["hard_cuts"]))
    return "；".join(parts)


def _why(e: Exception) -> str:
    return (str(e).strip().splitlines() or [type(e).__name__])[0][:200]


def scene_qc(ctx, sv, path, code: str, first=None) -> dict | None:
    """出片自检：找片段内部的硬切；给了首帧图时再看视频开头是不是从首帧开始。
    检测本身失败只记日志，不影响出片。"""
    qc: dict = {}
    detect = getattr(sv.ffmpeg, "scene_cuts", None)
    if detect is not None:
        try:
            cuts = detect(path)
        except JobCanceled:
            raise
        except Exception as e:
            ctx.log(f"视频 {code}：跳切检测没跑成，跳过（{_why(e)}）")
        else:
            if cuts:
                ctx.log(f"视频 {code}：{cut_note(cuts)}")
            qc["hard_cuts"] = cuts
    similar = getattr(sv.ffmpeg, "first_frame_ssim", None)
    if similar is not None and first is not None:
        try:
            v = float(similar(path, first))
        except JobCanceled:
            raise
        except Exception as e:
            ctx.log(f"视频 {code}：首帧相似度检测没跑成，跳过（{_why(e)}）")
        else:
            qc["first_frame_ssim"] = round(v, 3)
            qc["start_mismatch"] = v < START_SSIM_MIN
            if qc["start_mismatch"]:
                ctx.log(f"视频 {code}：{start_note(v)}")
    return qc or None


def clean_selection(s, shot_id: int, fresh_hashes) -> bool:
    """当前选定的视频是不是「干净的」：出片自检没发现问题（疑似跳切、开头没接上首帧），而且输入没过期。"""
    cur = selected_candidate(s, "shot", shot_id, "video")
    if cur is None or cur.input_hash not in fresh_hashes:
        return False
    return not qc_bad((cur.params or {}).get("qc"))


def _speakers(proj, shot) -> dict[str, Speaker]:
    """说话人叫法：画面里的人用分镜在 image_prompt 里给他的简短指代（和 motion 里的叫法一致），
    没有时用外貌简称；性别从外貌第一句看（画外音要说清男声女声）。只露手 / 腰的插入镜头里没人露脸，
    都按不在画面里算（不加视线句，说话就是画外音）。"""
    on = {c.get("character_id") for c in shot.cast or [] if isinstance(c, dict)}
    if insert_kind(shot.image_prompt or "", getattr(shot, "shot_size", "") or "") in ("hands", "body"):
        on = set()
    refs = token_refs(shot.image_prompt)
    return {c.name: Speaker(refs.get(c.id) or short_ref(c.visual_prompt), c.id in on,
                            gender_of(c.visual_prompt))
            for c in proj.characters}


def _shot_location(s, shot) -> str:
    """这一镜所在段落的场景外观摘要（英文）；手工镜头没挂段落、场景没写外观时为空。"""
    blk = s.get(SceneBlock, shot.scene_block_id) if shot.scene_block_id else None
    loc = s.get(Location, blk.location_id) if blk is not None and blk.location_id else None
    return location_summary(loc.visual_prompt) if loc is not None else ""


def image_max_refs(engines, name: str) -> int:
    try:
        return engines.image(name).caps.max_refs
    except KeyError:
        return 0


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
        # 发给引擎的版本：说话人用英文简称、画外音写明；输入哈希仍按老写法算（见 compose_video_prompt）
        prompt = compose_video_prompt(shot.motion_prompt, shot_image_prompt(shot, proj),
                                      shot.audio_prompt,
                                      shot.dialogue, anchor=style.anchor_text if style else "",
                                      has_audio=has_audio, speakers=_speakers(proj, shot))
        negative = join_negative(proj.negative, style.negative if style else "")
        old_pack = legacy_video_refs(s, shot)   # 以前每人带定妆照 + 三视图格（审查 P1-9 之前）
        legacy = old_pack[:engine.caps.max_refs]   # 更早的规则：不管首帧，一律带定妆照 / 三视图
        plan = video_ref_plan(s, shot, ff, lambda n: image_max_refs(sv.engines, n))
        old_plan = video_ref_plan(s, shot, ff, lambda n: image_max_refs(sv.engines, n),
                                  packed=old_pack)
        if plan.dropped:
            src = ("首帧是上一镜视频的尾帧，上一镜没用定妆照锁脸" if plan.continued
                   else f"首帧是 {ff.engine} 出的图，没用定妆照锁脸")
            ctx.log(f"视频 {shot.code}：{src}，身份以首帧为准，"
                    f"不带{'、'.join(r.label for r in plan.dropped)}（免得首帧和参考图两张脸打架）")
        if plan.continued and plan.entering and plan.identity == "entering":
            ctx.log(f"视频 {shot.code}：接上一镜，{'、'.join(r.label for r in plan.refs)} 是新入画的角色，"
                    "带上定妆照锁脸")
        packed = plan.refs
        refs = [sv.media.abs(r.file_path) for r in packed]
        labels = ref_labels(packed, refs)
        used = packed[:engine.caps.max_refs]
        h = video_input_hash(shot, proj, style, ff.id, engine_name, has_audio,
                             [r.candidate_id for r in used])
        # 按以前规则出的视频（输入哈希按当时带的参考图算）不算过期，不白跑 GPU：
        # 一律带参考图的老规则；接上一镜以前一律不带参考图
        h_legacy = video_input_hash(shot, proj, style, ff.id, engine_name, has_audio,
                                    [r.candidate_id for r in legacy])
        ok_hashes = {h, h_legacy,
                     video_input_hash(shot, proj, style, ff.id, engine_name, has_audio,
                                      [r.candidate_id for r in old_plan.refs[:engine.caps.max_refs]])}
        if plan.continued:
            ok_hashes.add(video_input_hash(shot, proj, style, ff.id, engine_name, has_audio, []))
        inputs = video_inputs(shot, proj, style, ff.id, engine_name, has_audio, used)
        if not payload.get("force"):
            cur = selected_candidate(s, "shot", shot.id, "video")
            if cur is not None and cur.input_hash in ok_hashes:  # hash 里含首帧候选 id
                ctx.log(f"视频 {shot.code}：输入没变，跳过（要重出用 --force）")
                return {"skipped": True, "candidate_ids": []}
        slot = get_or_create_slot(s, "shot", shot.id, "video")
        seed = shot_seed(proj.seed, shot.id, len(slot.candidates))
        seconds = max(1.0, min(float(shot.duration), engine.caps.max_seconds))
        # 最后一句台词说完再留 0.3 秒：帧数网格往下取时不能把台词截掉
        min_seconds = min(max((float(d.get("end") or 0) for d in shot.dialogue or []),
                              default=0.0) + 0.3 if shot.dialogue else 0.0, seconds + 1.0)
        first = sv.media.abs(ff.file_path)
        slug, code, aspect = proj.slug, shot.code, proj.aspect
        place = _shot_location(s, shot)
        extra = []
        if plan.continued and plan.entering:
            looks = {c.id: c.visual_prompt for c in proj.characters}
            extra.append(entering_clause([looks.get(cid) or "" for cid in plan.entering]))
        on_screen = any(isinstance(c, dict) and c.get("character_id") is not None
                        for c in shot.cast or [])
        if on_screen and ep.shots and ep.shots[-1].id == shot.id:
            extra.append(STAY_IN_FRAME)   # 全集最后一镜：人留在画面里，结尾的压迫感不散
        auto_retry = bool(getattr(proj, "auto_retry_video", False))
        base_seed, taken = proj.seed, len(slot.candidates)
    cids: list[int] = []
    retry_of: int | None = None
    for attempt in range(2):
        if attempt:
            # 自动重拍：同一个任务里换种子再出一条（只一次）。在同一个任务里做，接上一镜的下一镜
            # （依赖这个任务）截尾帧时拿到的就是重拍后的选定视频
            seed = shot_seed(base_seed, shot_id, taken + attempt)
        # 连续性要求只加在发给引擎的提示词里，不进输入哈希
        call = engine.build(VideoRequest(prompt=with_continuity(prompt, place, extra),
                                         first_frame=first, aspect=aspect, seed=seed,
                                         seconds=seconds, min_seconds=min_seconds,
                                         negative=negative, refs=refs,
                                         prefix=f"studio/{slug}/clip_{code}"))
        ctx.log(f"视频 {code} · {engine_name} · {seconds:.1f}s · seed {seed} · 参考图 {len(refs)} 张"
                f"{'（原生有声）' if has_audio else '（无声）'}"
                f"{'（自动重拍）' if attempt else ''}"
                f"{'：' + '、'.join(labels.values()) if labels else ''}")
        files, run_dir = run_comfy(ctx, call, slug, labels)
        video = next((f for f in files if f.suffix.lower() in VIDEO_EXTS), None)
        if video is None:
            raise JobError(f"镜头 {code} 没有产出视频文件：{[f.name for f in files]}")
        dst = sv.media.new_media_path(slug, "video", video.suffix)
        shutil.copyfile(video, dst)
        canceled: JobCanceled | None = None
        try:
            qc = scene_qc(ctx, sv, dst, code, first)
        except JobCanceled as e:
            # 视频已经出好了：先登记成候选（没做自检），再把取消报上去，GPU 的活不白干
            qc, canceled = None, e
            ctx.log(f"视频 {code}：跳切检测时任务被取消，视频先登记成候选（没做跳切自检）")
        with sv.db.session() as s:
            slot = get_or_create_slot(s, "shot", shot_id, "video")
            params = {**call.meta, "first_frame_candidate_id": ff.id,
                      "identity": plan.identity, "locked_chars": plan.locked_chars,
                      "workflow": sv.media.rel(run_dir / "workflow.api.json"), "inputs": inputs}
            if qc is not None:
                params["qc"] = qc
            if retry_of is not None:
                params["retry_of"] = retry_of
            if retry_of is None:
                # 新的一条自检有问题（疑似跳切 / 开头没接上首帧）、而当前选定的是干净且没过期的：
                # 不抢选定（无人值守时坏片不进成片），重拍也一样
                keep = qc_bad(qc) and clean_selection(s, shot_id, ok_hashes)
                force_select = bool(payload.get("force")) and not keep   # 重拍：新的一条直接选中
            else:
                # 自动重拍的这条：干净就换上它（第一条开头没接上）；还是有问题就不抢，留着第一条
                keep = qc_bad(qc)
                force_select = not keep
            c = add_candidate(s, slot, file_path=sv.media.rel(dst), engine=engine_name,
                              params=params, input_hash=h, job_id=ctx.job_id,
                              auto_select=not keep, force_select=force_select)
            if keep and retry_of is None:
                ctx.log(f"视频 {code}：新的一条自检有问题，没有替换当前选定的视频；要用它请手动选")
            if qc_bad(qc):
                c.note = qc_note(qc)
            cid = c.id
        cids.append(cid)
        if canceled is not None:
            raise canceled
        if attempt or not (auto_retry and qc and qc.get("start_mismatch")):
            break
        retry_of = cid
        ctx.log(f"视频 {code}：开头没接上首帧，项目开了「自动重拍」，换种子再出一条（只重拍一次）")
    if retry_of is not None:
        ctx.log(f"视频 {code}：自动重拍"
                + ("这条接上了首帧，已选用它" if not qc_bad(qc) else
                   "的这条还是有问题，没有替换第一条；两条都留在候选里，看一下再选"))
    out = {"candidate_ids": cids, "file": sv.media.rel(dst)}
    if qc is not None:
        out["qc"] = qc
    return out
