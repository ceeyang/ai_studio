"""接上一镜（image.continue_frame）：开了 Shot.continue_from_prev 的镜头不出图，首帧直接取上一镜选定视频的
最后一帧（ffmpeg 截尾帧），背景、人物位置和光线都接得上。

首帧候选的 engine 记成 "continue"，输入哈希只跟上一镜选定的视频候选 id 走：上一镜换了视频，这一镜的
首帧就过期，重新截一帧。上一镜还没有选定视频时直接失败（中文原因），界面显示「等上一镜视频」。
整集 / 多镜一起出视频时，submit_chain 把这一步排在上一镜的视频任务之后、这一镜的视频任务之前。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from backend.domain.hashing import input_hash
from backend.domain.models import Candidate, Shot
from backend.domain.refs import Ref, shot_refs
from backend.domain.shots import prev_shot
from backend.domain.slots import add_candidate, get_or_create_slot, selected_candidate
from backend.pipeline.common import job_services, load_episode
from backend.pipeline.prompting import first_frame_locked_face
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


def _cast_ids(shot) -> list[int]:
    return [c["character_id"] for c in shot.cast or []
            if isinstance(c, dict) and c.get("character_id") is not None]


def _tail_locked(s, ff, image_max_refs: Callable[[str], int], depth: int = 0) -> set[int]:
    """接上一镜的首帧（上一镜视频的尾帧）里，哪些角色的脸是按定妆照锁过的。看上一镜视频：
    params["locked_chars"] 记着锁过脸的角色（新视频都记）；老视频只有 identity：refs = 上一镜出场角色全锁，
    first_frame / entering = 都没锁（entering 没记名单时保守按没锁算）；连 identity 都没有的看它用的首帧
    （可能又是接上一镜的）。"""
    vid = s.get(Candidate, (ff.params or {}).get("prev_video_candidate_id") or 0)
    if vid is None:
        return set()
    vp = vid.params or {}
    if isinstance(vp.get("locked_chars"), list):
        return {int(x) for x in vp["locked_chars"]}
    prev = s.get(Shot, (ff.params or {}).get("prev_shot_id") or 0)
    everyone = set(_cast_ids(prev)) if prev is not None else set()
    ident = vp.get("identity")
    if ident:
        return everyone if ident == "refs" else set()
    pff = s.get(Candidate, vp.get("first_frame_candidate_id") or 0)
    if pff is None:
        return set()
    if pff.engine == CONTINUE_ENGINE:
        if depth >= 8 or not _tail_locked(s, pff, image_max_refs, depth + 1) >= everyone:
            return set()
        return everyone
    return everyone if first_frame_locked_face(pff.params, image_max_refs(pff.engine)) else set()


@dataclass
class VideoRefPlan:
    """视频要带哪些参考图（还没按视频引擎上限截断）。
    identity 记进视频候选：refs = 出场角色的脸都锁过、都带；entering = 接上一镜、只带部分角色（新入画的、
    尾帧里本来就锁过脸的）；first_frame = 不带，身份以首帧为准。locked_chars 也记进候选，下一镜接这一镜时用。"""
    refs: list[Ref]
    locked: bool
    identity: str
    continued: bool = False
    entering: list[int] = field(default_factory=list)   # 接上一镜：这一镜新入画的角色 id
    dropped: list[Ref] = field(default_factory=list)    # 没带的（日志用）
    locked_chars: list[int] = field(default_factory=list)   # 这条视频里脸按定妆照锁过的角色


def _ref_chars(refs: list[Ref]) -> list[int]:
    return sorted({r.character_id for r in refs if r.character_id is not None})


def video_ref_plan(s, shot, ff, image_max_refs: Callable[[str], int]) -> VideoRefPlan:
    """选定首帧 ff 下视频带哪些参考图。videos.run_video 和 views.shot_detail（过期判断）共用。

    - 普通首帧：锁过脸（带了定妆照 / 三视图）就带全部出场角色的定妆照，没锁就一张不带（两张脸会打架）；
    - 接上一镜的首帧：尾帧里锁过脸的角色接着带（沿用上一镜）；这一镜 cast 里有、上一镜 cast 里没有的角色
      （从画外进来，尾帧里还没有这个人）总要带上定妆照——不然 H3 会自己编一张脸（第三次样片 S06）。"""
    packed = shot_refs(s, shot, video=True)
    if ff is None or ff.engine != CONTINUE_ENGINE:
        locked = ff is not None and first_frame_locked_face(ff.params,
                                                            image_max_refs(ff.engine))
        return VideoRefPlan(packed if locked else [], locked, "refs" if locked else "first_frame",
                            dropped=[] if locked else packed,
                            locked_chars=_ref_chars(packed) if locked else [])
    prev = s.get(Shot, (ff.params or {}).get("prev_shot_id") or 0) or prev_shot(s, shot)
    before = set(_cast_ids(prev)) if prev is not None else set()
    cast = list(dict.fromkeys(_cast_ids(shot)))
    entering = [cid for cid in cast if cid not in before]
    keep = set(entering) | _tail_locked(s, ff, image_max_refs)
    refs = [r for r in packed if r.character_id in keep]
    with_refs = {r.character_id for r in packed}
    full = bool(refs) and all(cid in keep for cid in cast if cid in with_refs)
    identity = "refs" if full else "entering" if refs else "first_frame"
    return VideoRefPlan(refs, full, identity, continued=True, entering=entering,
                        dropped=[r for r in packed if r not in refs], locked_chars=_ref_chars(refs))


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
        # 尾帧是确定的：上一镜视频没换就直接复用，强制重出（重拍这一镜的视频）也不再截一张一样的
        cur = selected_candidate(s, "shot", shot.id, "first_frame")
        if cur is not None and cur.input_hash == h and sv.media.abs(cur.file_path).is_file():
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
