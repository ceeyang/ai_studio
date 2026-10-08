"""把"跑某几个阶段"拆成任务并串好依赖。API、CLI 和阶段处理器（then 链）都用它。"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from backend.domain.db import Database
from backend.domain.models import Episode
from backend.queue.errors import JobError

STAGES = ("script", "storyboard", "keyframes", "videos", "export")
LLM_STAGES = ("script", "storyboard")


@dataclass
class RunOptions:
    shots: list[str] | None = None
    candidates: int = 1
    force: bool = False
    priority: int = 0


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
        return (engines.image(name) if kind == "image" else engines.video(name)).family
    except KeyError as e:
        raise JobError(str(e)) from None


def submit_chain(manager, db: Database, engines, episode_id: int, stages: list[str],
                 opts: RunOptions, depends_on: list[int] | None = None) -> list[int]:
    stages = list(stages)
    bad = [x for x in stages if x not in STAGES]
    if bad:
        raise JobError(f"未知阶段：{', '.join(bad)}，可选：{', '.join(STAGES)}")
    if not stages:
        return []
    if any(x in LLM_STAGES for x in stages[1:]) and stages[0] not in LLM_STAGES:
        raise JobError("script / storyboard 必须排在最前面")
    deps = list(depends_on or [])
    with db.session() as s:
        ep = s.get(Episode, episode_id)
        if ep is None:
            raise LookupError(f"找不到剧集 {episode_id}")
        proj = ep.project
        common = {"project_id": proj.id, "priority": opts.priority}
        picked = [] if stages[0] in LLM_STAGES else [
            (sh.id, sh.image_engine or proj.image_engine, sh.video_engine or proj.video_engine)
            for sh in _pick_shots(ep.shots, opts.shots)]

    first = stages[0]
    if first in LLM_STAGES:
        payload: dict = {"episode_id": episode_id, "force": opts.force}
        if stages[1:]:
            payload["then"] = stages[1:]
            payload["options"] = asdict(opts)
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
        ids.append(manager.submit("export.episode", {"episode_id": episode_id},
                                  owner_type="episode", owner_id=episode_id,
                                  depends_on=deps + upstream, **common))
    return ids
