"""接上一镜（image.continue_frame）：开了 Shot.continue_from_prev 的镜头不出图，首帧直接取上一镜选定视频的
最后一帧（ffmpeg 截尾帧），背景、人物位置和光线都接得上。

首帧候选的 engine 记成 "continue"，输入哈希只跟上一镜选定的视频候选 id 走：上一镜换了视频，这一镜的
首帧就过期，重新截一帧。上一镜还没有选定视频时直接失败（中文原因），界面显示「等上一镜视频」。
整集 / 多镜一起出视频时，submit_chain 把这一步排在上一镜的视频任务之后、这一镜的视频任务之前。
"""
from __future__ import annotations

from backend.domain.hashing import input_hash
from backend.domain.models import Shot
from backend.domain.shots import prev_shot
from backend.domain.slots import add_candidate, get_or_create_slot, selected_candidate
from backend.pipeline.common import job_services, load_episode
from backend.queue.errors import JobError
from backend.queue.handlers import HANDLERS

CONTINUE_ENGINE = "continue"
TASK = "image.continue_frame"


def continue_hash(prev_video_candidate_id: int) -> str:
    return input_hash("continue_from_prev", int(prev_video_candidate_id))


def continue_inputs(prev_code: str, prev_video_candidate_id: int) -> dict:
    """首帧候选 params["inputs"]：过期原因（stale_reason）按 prev_video 比对。"""
    return {"engine": CONTINUE_ENGINE, "prev_shot": prev_code,
            "prev_video": int(prev_video_candidate_id), "refs": []}


def waiting_reason(code: str, prev) -> str:
    if prev is None:
        return f"{code} 是这一集的第一镜，没有上一镜可接，关掉「接上一镜」再出首帧"
    return f"{code} 接上一镜：上一镜 {prev.code} 还没有选定视频，先出上一镜的视频"


@HANDLERS.register(TASK)
def run_continue_frame(ctx, payload: dict) -> dict:
    sv = job_services(ctx)
    shot_id = int(payload["shot_id"])
    with sv.db.session() as s:
        shot = s.get(Shot, shot_id)
        if shot is None:
            raise JobError(f"找不到镜头 {shot_id}")
        code = shot.code
        if not shot.continue_from_prev:
            raise JobError(f"镜头 {code} 没有开「接上一镜」")
        prev = prev_shot(s, shot)
        video = selected_candidate(s, "shot", prev.id, "video") if prev is not None else None
        if video is None:
            raise JobError(waiting_reason(code, prev))
        h = continue_hash(video.id)
        inputs = continue_inputs(prev.code, video.id)
        if not payload.get("force"):
            cur = selected_candidate(s, "shot", shot.id, "first_frame")
            if cur is not None and cur.input_hash == h:
                ctx.log(f"首帧 {code}：上一镜 {prev.code} 的视频没换，尾帧已是最新，跳过")
                return {"skipped": True, "candidate_ids": []}
        src = sv.media.abs(video.file_path)
        _, proj = load_episode(s, shot.episode_id)
        slug, prev_code, prev_id, video_id, video_rel = (proj.slug, prev.code, prev.id, video.id,
                                                         video.file_path)
    if not src.is_file():
        raise JobError(f"{code} 接上一镜：上一镜 {prev_code} 的视频文件不见了（{video_rel}）")
    dst = sv.media.new_media_path(slug, "first_frame", ".png")
    ctx.log(f"首帧 {code}：取上一镜 {prev_code} 视频（候选 {video_id}）的最后一帧")
    sv.ffmpeg.last_frame(src, dst)
    with sv.db.session() as s:
        slot = get_or_create_slot(s, "shot", shot_id, "first_frame")
        # 尾帧是确定的：新截的这张总是选中（上一镜换了视频，旧的那张已经接不上了）
        c = add_candidate(s, slot, file_path=sv.media.rel(dst), engine=CONTINUE_ENGINE,
                          params={"prev_shot_id": prev_id, "prev_video_candidate_id": video_id,
                                  "inputs": inputs},
                          input_hash=h, job_id=ctx.job_id, force_select=True)
        cid = c.id
    return {"candidate_ids": [cid], "file": sv.media.rel(dst)}
