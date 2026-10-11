"""把"跑某几个阶段"拆成任务并串好依赖。API、CLI 和阶段处理器（then 链）都用它。"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace

from sqlalchemy import select

from backend.domain.db import Database
from backend.domain.models import Episode, Slot
from backend.engines.registry import check_ready, runtime_of
from backend.pipeline.common import check_scene_blocks, clean_note
from backend.pipeline.post import lines_missing_km, speakers_without_voice
from backend.post.options import ExportOptions
from backend.queue.errors import JobError

STAGES = ("script", "storyboard", "keyframes", "videos", "export")
LLM_STAGES = ("script", "storyboard")
# 不在 all 里的阶段：按高棉语时长改写中文（之后通常接 videos、export）
FIT_STAGE = "fit_khmer"
EXTRA_STAGES = (FIT_STAGE,)
FIT_TASK = "dialogue.fit_khmer"
CONTINUE_TASK = "image.continue_frame"   # pipeline/continuity.py：接上一镜，取上一镜视频的尾帧


@dataclass
class RunOptions:
    shots: list[str] | None = None
    candidates: int = 1
    force: bool = False
    priority: int = 0
    export: dict | None = None   # ExportOptions 的字段：subtitles / dub / end_card / trim
    scene_block_ids: list[int] | None = None   # 只重写这些段落的分镜（只对 storyboard 有效）
    note: str | None = None      # 修改意见：写进 script / storyboard 提示词，非空时等同 force


@dataclass
class Chain:
    """接上一镜：开了 continue_from_prev 的镜头 id，以及每个镜头的上一镜 id（按集内顺序）。"""
    cont: set[int] = field(default_factory=set)
    prev: dict[int, int] = field(default_factory=dict)
    order: list[int] = field(default_factory=list)
    # 镜头 id → 它排队 / 正在跑的视频任务（最新的那个）：上一镜的视频还在出时，截尾帧排在它后面
    pending_video: dict[int, int] = field(default_factory=dict)


@dataclass
class Precheck:
    """用户从界面 / API 点「跑」时的提交前校验（阶段处理器接着提交后续阶段时不做）。
    comfy：返回 (ComfyUI 连得上吗, 地址)；None 表示不查 ComfyUI。"""
    comfy: Callable[[], tuple[bool, str]] | None = None


def _prev_has_video(s, chain: "Chain") -> set[int]:
    """接上一镜的镜头里，上一镜已有选定视频的（只跑首帧时能直接截尾帧）。"""
    prevs = {chain.prev[sid] for sid in chain.cont if sid in chain.prev}
    if not prevs:
        return set()
    with_video = set(s.scalars(select(Slot.owner_id).where(
        Slot.owner_type == "shot", Slot.kind == "video", Slot.owner_id.in_(list(prevs)),
        Slot.selected_candidate_id.is_not(None))))
    return {sid for sid in chain.cont if chain.prev.get(sid) in with_video}


def _waits_prev_video(sid: int, chain: "Chain", stages: list[str], ready: set[int]) -> bool:
    """只跑首帧（不连视频）时，接上一镜而上一镜既没有选定视频、也没有视频任务在排队的镜头：
    截尾帧注定失败（第三次样片 job 217），提交时跳过，等上一镜出了视频再说。"""
    return ("keyframes" in stages and "videos" not in stages and sid in chain.cont
            and sid not in ready and chain.prev.get(sid) not in chain.pending_video)


def _chain_of(s, ep) -> "Chain":
    order = [sh.id for sh in ep.shots]
    chain = Chain(cont={sh.id for sh in ep.shots if sh.continue_from_prev},
                  prev={b: a for a, b in zip(order, order[1:])}, order=order)
    if chain.cont:
        from backend.domain.models import Job
        from backend.queue.manager import ACTIVE
        for oid, jid in s.execute(select(Job.owner_id, Job.id).where(
                Job.type == "video.shot", Job.owner_type == "shot",
                Job.owner_id.in_(order), Job.status.in_(ACTIVE)).order_by(Job.id)):
            chain.pending_video[oid] = jid
    return chain


def continue_wait_notes(db: Database, episode_id: int, stages: list[str],
                        shots: list[str] | None = None) -> list[str]:
    """只跑首帧时被跳过的接上一镜镜头（见 _waits_prev_video），给提交结果里的一句说明。"""
    if "keyframes" not in stages or "videos" in stages:
        return []
    with db.session() as s:
        ep = s.get(Episode, episode_id)
        if ep is None or not ep.shots:
            return []
        try:
            picked = _pick_shots(ep.shots, shots)
        except JobError:
            return []
        chain = _chain_of(s, ep)
        ready = _prev_has_video(s, chain)
        codes = {sh.id: sh.code for sh in ep.shots}
        waiting = [(sh.code, codes.get(chain.prev.get(sh.id), ""))
                   for sh in picked if _waits_prev_video(sh.id, chain, stages, ready)]
    if not waiting:
        return []
    what = "、".join(f"{c}（接 {p}）" if p else c for c, p in waiting)
    return [f"{what} 等上一镜视频：首帧要取上一镜视频的最后一帧，这次先跳过；"
            "出完上一镜的视频后再出，或者直接连视频一起生成（会自动按顺序串起来）"]


def _pick_shots(shots, wanted: list[str] | None):
    if not wanted:
        return list(shots)
    by_code = {sh.code.upper(): sh for sh in shots}
    by_id = {str(sh.id): sh for sh in shots}
    out = []
    for tok in wanted:
        t = str(tok).strip()
        sh = by_code.get(t.upper()) or by_id.get(t)
        if sh is None:
            raise JobError(f"找不到镜头：{t}")
        if sh not in out:
            out.append(sh)
    return out


def _family(engines, kind: str, name: str) -> str:
    try:
        eng = engines.image(name) if kind == "image" else engines.video(name)
    except KeyError as e:
        raise JobError(str(e)) from None
    check_ready(eng)
    return eng.family


def _uses_comfy(engines, kind: str, names) -> bool:
    for name in set(names):
        try:
            eng = engines.image(name) if kind == "image" else engines.video(name)
        except KeyError:
            continue  # 引擎不存在由 _family 报更准确的错
        if runtime_of(eng) == "comfyui":
            return True
    return False


def _run_precheck(check: Precheck, engines, stages: list[str], picked, codes: dict[int, str],
                  selected: set[tuple[int, str]], chain: "Chain | None" = None) -> None:
    """缺首帧就出视频、缺视频就导出、要用的引擎跑在 ComfyUI 上而 ComfyUI 连不上：提交前直接拒绝。
    接上一镜的镜头出视频前会先取上一镜的尾帧：上一镜有视频、或这一批里会出上一镜的视频就不算缺首帧。"""
    chain = chain or Chain()
    if "videos" in stages and "keyframes" not in stages:
        batch = {sid for sid, _, _ in picked}
        missing = []
        for sid, _, _ in picked:
            if sid in chain.cont:
                pv = chain.prev.get(sid)
                if pv is not None and ((pv, "video") in selected or pv in batch
                                       or pv in chain.pending_video):
                    continue
                missing.append(f"{codes[sid]}（接上一镜，上一镜"
                               f"{' ' + codes[pv] if pv in codes else ''} 还没有视频）")
            elif (sid, "first_frame") not in selected:
                missing.append(codes[sid])
        if missing:
            raise JobError(f"{'、'.join(missing)} 还没有选定首帧，先生成首帧")
    if "export" in stages and "videos" not in stages and "keyframes" not in stages:
        missing = [code for sid, code in codes.items() if (sid, "video") not in selected]
        if missing:
            raise JobError(f"以下镜头还没有选定视频：{', '.join(missing)}")
    if check.comfy is None:
        return
    need = (("keyframes" in stages
             and _uses_comfy(engines, "image", [ie for sid, ie, _ in picked if sid not in chain.cont]))
            or ("videos" in stages and _uses_comfy(engines, "video", [ve for _, _, ve in picked])))
    if need:
        ok, url = check.comfy()
        if not ok:
            raise JobError(f"ComfyUI 连不上（{url}），先启动 ComfyUI 或到设置页检查")


def portrait_warnings(db: Database, engines, episode_id: int, stages: list[str],
                      shots: list[str] | None = None) -> list[str]:
    """出首帧之前：出场角色还没有选定定妆照、而这一镜的图像引擎要用定妆照锁脸时给一句提醒（不拦）。

    没有定妆照时首帧只按文字出脸；之后补了定妆照，这些首帧会因为参考图变了而过期重出——GPU 白跑一轮。
    整集 all 从剧本开始、还没有镜头时，按项目里所有角色和项目的图像引擎估计。"""
    if "keyframes" not in stages:
        return []
    from backend.domain.characters import portrait_of
    from backend.domain.models import Character

    def max_refs(name: str) -> int:
        try:
            return engines.image(name).caps.max_refs
        except KeyError:
            return 0

    with db.session() as s:
        ep = s.get(Episode, episode_id)
        if ep is None:
            return []
        proj = ep.project
        try:
            picked = _pick_shots(ep.shots, shots) if ep.shots else []
        except JobError:
            return []
        locking: dict[int, set[str]] = {}   # 角色 id → 要锁脸的图像引擎
        if picked:
            for sh in picked:
                if sh.continue_from_prev:
                    continue   # 接上一镜：首帧是上一镜的尾帧，不出图
                eng = sh.image_engine or proj.image_engine
                if max_refs(eng) > 0:
                    for c in sh.cast or []:
                        if isinstance(c, dict) and c.get("character_id") is not None:
                            locking.setdefault(c["character_id"], set()).add(eng)
        elif max_refs(proj.image_engine) > 0:
            locking = {c.id: {proj.image_engine} for c in proj.characters}
        missing = []
        for cid in sorted(locking):
            ch = s.get(Character, cid)
            if ch is not None and portrait_of(s, cid) is None:
                missing.append((ch.name, sorted(locking[cid])))
    if not missing:
        return []
    names = "、".join(n for n, _ in missing)
    engs = "、".join(sorted({e for _, es in missing for e in es}))
    return [f"{names} 还没有定妆照：{engs} 出首帧要用定妆照锁脸，现在只能按文字出脸；之后补了定妆照，"
            "这些首帧会过期重出。建议先到「角色」阶段抽定妆照（或 studio character portrait 上传）再出首帧"]


def submit_chain(manager, db: Database, engines, episode_id: int, stages: list[str],
                 opts: RunOptions, depends_on: list[int] | None = None, *,
                 precheck: Precheck | None = None) -> list[int]:
    stages = list(stages)
    bad = [x for x in stages if x not in STAGES + EXTRA_STAGES]
    if bad:
        raise JobError(f"未知阶段：{', '.join(bad)}，可选：{', '.join(STAGES + EXTRA_STAGES)}")
    if not stages:
        return []
    if any(x in LLM_STAGES for x in stages[1:]) and stages[0] not in LLM_STAGES:
        raise JobError("script / storyboard 必须排在最前面")
    if FIT_STAGE in stages[1:]:
        raise JobError(f"{FIT_STAGE} 必须排在最前面（它改完台词才能出视频、导出）")
    deps = list(depends_on or [])
    note = clean_note(opts.note)
    with db.session() as s:
        ep = s.get(Episode, episode_id)
        if ep is None:
            raise LookupError(f"找不到剧集 {episode_id}")
        if opts.scene_block_ids:
            check_scene_blocks(s, episode_id, stages[0], opts.scene_block_ids)
        proj = ep.project
        common = {"project_id": proj.id, "priority": opts.priority}
        picked = [] if stages[0] in LLM_STAGES else [
            (sh.id, sh.image_engine or proj.image_engine, sh.video_engine or proj.video_engine)
            for sh in _pick_shots(ep.shots, opts.shots)]
        dialogue = [list(sh.dialogue or []) for sh in ep.shots]
        codes = [sh.code for sh in ep.shots]
        voices = {c.name: c.voice for c in proj.characters}
        shot_codes = {sh.id: sh.code for sh in ep.shots}
        if "export" in stages and (opts.export or {}).get("music"):
            from backend.domain.music import bgm_of
            if bgm_of(s, episode_id) is None:   # 在提交任何任务之前就拦下，免得前面的任务成了孤儿
                raise JobError("这一集还没有上传配乐，先在成片阶段上传背景音乐，或取消勾选「配乐」")
        chain = _chain_of(s, ep)
        ready = _prev_has_video(s, chain)
        if stages[0] not in LLM_STAGES and stages[0] != FIT_STAGE:
            # 只跑首帧时，等上一镜视频的接上一镜镜头不提交（不出一条注定失败的任务）
            picked = [p for p in picked if not _waits_prev_video(p[0], chain, stages, ready)]
        selected: set[tuple[int, str]] = set()
        if precheck is not None and stages[0] not in LLM_STAGES and shot_codes:
            selected = {(oid, kind) for oid, kind in s.execute(
                select(Slot.owner_id, Slot.kind).where(
                    Slot.owner_type == "shot", Slot.owner_id.in_(list(shot_codes)),
                    Slot.selected_candidate_id.is_not(None)))}

    if precheck is not None and stages[0] not in LLM_STAGES:
        _run_precheck(precheck, engines, stages, picked, shot_codes, selected, chain)

    first = stages[0]
    if first == FIT_STAGE:
        fit: dict = {"episode_id": episode_id, "force": opts.force}
        if opts.shots:
            fit["shot_ids"] = [sid for sid, _, _ in picked]
        if stages[1:]:
            fit["then"] = stages[1:]
            fit["options"] = asdict(opts)
        return [manager.submit(FIT_TASK, fit, owner_type="episode", owner_id=episode_id,
                               depends_on=deps, **common)]
    if first in LLM_STAGES:
        payload: dict = {"episode_id": episode_id, "force": opts.force or note is not None}
        if note:
            payload["note"] = note
        if opts.scene_block_ids:
            payload["scene_block_ids"] = list(opts.scene_block_ids)
        if stages[1:]:
            payload["then"] = stages[1:]
            # 修改意见和按段只作用于这一阶段，不带进后续阶段
            payload["options"] = asdict(replace(opts, note=None, scene_block_ids=None))
        return [manager.submit(first, payload, owner_type="episode", owner_id=episode_id,
                               depends_on=deps, **common)]

    if ("keyframes" in stages or "videos" in stages) and not picked:
        if shot_codes and "videos" not in stages:
            return []   # 选中的都是等上一镜视频的接上一镜镜头：跳过，说明见 continue_wait_notes
        raise JobError("这一集还没有分镜，先跑 storyboard")
    # 先把本次会用到的引擎 family 全部解析出来，任何一个镜头的引擎不存在都在提交任何任务之前报错，
    # 避免前面镜头的任务已经提交、后面镜头才校验失败导致的孤儿任务。
    img_fam: dict[int, str] = {}
    vid_fam: dict[int, str] = {}
    if "keyframes" in stages:
        for sid, image_engine, _ in picked:
            if sid not in chain.cont:   # 接上一镜的不出图，不用图像引擎
                img_fam[sid] = _family(engines, "image", image_engine)
    if "videos" in stages:
        for sid, _, video_engine in picked:
            vid_fam[sid] = _family(engines, "video", video_engine)

    ids: list[int] = []
    kf: dict[int, list[int]] = {}
    vids: list[int] = []
    if "keyframes" in stages:
        for sid, _, _ in picked:
            if sid in chain.cont:
                # 接上一镜：取上一镜视频的尾帧（CPU 任务）。同时出视频时放到视频循环里，排在上一镜视频之后
                if "videos" not in stages:
                    pv = chain.prev.get(sid)
                    wait = [chain.pending_video[pv]] if pv in chain.pending_video else []
                    ids.append(manager.submit(CONTINUE_TASK, {"shot_id": sid, "force": opts.force},
                                              owner_type="shot", owner_id=sid,
                                              depends_on=deps + wait, **common))
                continue
            fam = img_fam[sid]
            kf_payload = {"shot_id": sid, "force": opts.force, "candidates": max(1, opts.candidates)}
            kf[sid] = [manager.submit("image.keyframe", kf_payload, owner_type="shot",
                                      owner_id=sid, model_family=fam, depends_on=deps, **common)
                       for _ in range(max(1, opts.candidates))]
            ids += kf[sid]
    if "videos" in stages:
        vid_of: dict[int, int] = {}
        todo = [sid for sid, _, _ in picked]
        if any(sid in chain.cont for sid in todo):   # 有接上一镜的：按集内顺序排，上一镜的视频先提交
            todo.sort(key=chain.order.index)
        for sid in todo:
            fam = vid_fam[sid]
            before = kf.get(sid, [])
            if sid in chain.cont:
                pv = chain.prev.get(sid)
                cj = manager.submit(CONTINUE_TASK, {"shot_id": sid, "force": opts.force},
                                    owner_type="shot", owner_id=sid,
                                    depends_on=deps + ([vid_of[pv]] if pv in vid_of else
                                                       [chain.pending_video[pv]]
                                                       if pv in chain.pending_video else []),
                                    **common)
                ids.append(cj)
                before = [cj]
            jid = manager.submit("video.shot", {"shot_id": sid, "force": opts.force},
                                 owner_type="shot", owner_id=sid,
                                 model_family=fam, depends_on=deps + before, **common)
            vid_of[sid] = jid
            vids.append(jid)
            ids.append(jid)
    if "export" in stages:
        upstream = vids or [j for js in kf.values() for j in js]
        exp = ExportOptions(**(opts.export or {}))
        if exp.dub == "km":
            nov = speakers_without_voice(dialogue, voices)
            if nov:
                raise JobError(f"角色 {', '.join(nov)} 没有设置配音音色（如 km-KH-PisethNeural），"
                               "先在剧本页角色卡或 studio character voice 里填写")
        pre: list[int] = []
        if exp.needs_km and lines_missing_km(dialogue, codes):
            pre.append(manager.submit("subtitles.translate", {"episode_id": episode_id},
                                      owner_type="episode", owner_id=episode_id,
                                      depends_on=list(deps), **common))
        if exp.dub == "km":
            pre.append(manager.submit("post.dub", {"episode_id": episode_id,
                                                   "options": exp.model_dump()},
                                      owner_type="episode", owner_id=episode_id,
                                      depends_on=deps + upstream + pre, **common))
        payload = {"episode_id": episode_id}
        if exp != ExportOptions():
            payload["options"] = exp.model_dump()
        ids += pre
        ids.append(manager.submit("export.episode", payload, owner_type="episode",
                                  owner_id=episode_id, depends_on=deps + upstream + pre, **common))
    return ids
