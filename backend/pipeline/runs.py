"""把"跑某几个阶段"拆成任务并串好依赖。API、CLI 和阶段处理器（then 链）都用它。"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, replace

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
class Precheck:
    """用户从界面 / API 点「跑」时的提交前校验（阶段处理器接着提交后续阶段时不做）。
    comfy：返回 (ComfyUI 连得上吗, 地址)；None 表示不查 ComfyUI。"""
    comfy: Callable[[], tuple[bool, str]] | None = None


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
                  selected: set[tuple[int, str]]) -> None:
    """缺首帧就出视频、缺视频就导出、要用的引擎跑在 ComfyUI 上而 ComfyUI 连不上：提交前直接拒绝。"""
    if "videos" in stages and "keyframes" not in stages:
        missing = [codes[sid] for sid, _, _ in picked if (sid, "first_frame") not in selected]
        if missing:
            raise JobError(f"{'、'.join(missing)} 还没有选定首帧，先生成首帧")
    if "export" in stages and "videos" not in stages and "keyframes" not in stages:
        missing = [code for sid, code in codes.items() if (sid, "video") not in selected]
        if missing:
            raise JobError(f"以下镜头还没有选定视频：{', '.join(missing)}")
    if check.comfy is None:
        return
    need = (("keyframes" in stages and _uses_comfy(engines, "image", [ie for _, ie, _ in picked]))
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
        selected: set[tuple[int, str]] = set()
        if precheck is not None and stages[0] not in LLM_STAGES and shot_codes:
            selected = {(oid, kind) for oid, kind in s.execute(
                select(Slot.owner_id, Slot.kind).where(
                    Slot.owner_type == "shot", Slot.owner_id.in_(list(shot_codes)),
                    Slot.selected_candidate_id.is_not(None)))}

    if precheck is not None and stages[0] not in LLM_STAGES:
        _run_precheck(precheck, engines, stages, picked, shot_codes, selected)

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
        raise JobError("这一集还没有分镜，先跑 storyboard")
    # 先把本次会用到的引擎 family 全部解析出来，任何一个镜头的引擎不存在都在提交任何任务之前报错，
    # 避免前面镜头的任务已经提交、后面镜头才校验失败导致的孤儿任务。
    img_fam: dict[int, str] = {}
    vid_fam: dict[int, str] = {}
    if "keyframes" in stages:
        for sid, image_engine, _ in picked:
            img_fam[sid] = _family(engines, "image", image_engine)
    if "videos" in stages:
        for sid, _, video_engine in picked:
            vid_fam[sid] = _family(engines, "video", video_engine)

    ids: list[int] = []
    kf: dict[int, list[int]] = {}
    vids: list[int] = []
    if "keyframes" in stages:
        for sid, _, _ in picked:
            fam = img_fam[sid]
            kf_payload = {"shot_id": sid, "force": opts.force, "candidates": max(1, opts.candidates)}
            kf[sid] = [manager.submit("image.keyframe", kf_payload, owner_type="shot",
                                      owner_id=sid, model_family=fam, depends_on=deps, **common)
                       for _ in range(max(1, opts.candidates))]
            ids += kf[sid]
    if "videos" in stages:
        for sid, _, _ in picked:
            fam = vid_fam[sid]
            jid = manager.submit("video.shot", {"shot_id": sid, "force": opts.force},
                                 owner_type="shot", owner_id=sid,
                                 model_family=fam, depends_on=deps + kf.get(sid, []), **common)
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
