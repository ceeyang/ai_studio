"""抽卡统计：每个镜头的首帧 / 视频各生成了几次（成功的候选 + 失败的任务）、选的是第几条，
以及一集的合计（可用率、平均每镜几抽、按引擎的成功 / 失败）。

- 一次「抽」= 槽位里的一个候选（视频自动重试一个任务可能出两条，各算一抽）；
- 失败 = 镜头上 status=failed 的出图 / 视频任务（取消的不算）；失败任务的引擎取它最后一次引擎调用明细，
  没有明细时按镜头 / 项目当前的引擎记；
- 重跑分镜删掉的镜头，候选在 archived_candidates 里：不算进当前镜头，只在一集合计里单列「废弃镜头的抽卡」。
全部是按集的几条 group by 查询，不按镜头逐个查。"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from backend.domain.models import (ArchivedCandidate, Candidate, Episode, Job, JobTrace, Project,
                                   Shot, Slot)

KINDS = ("first_frame", "video")
# 镜头任务类型 → 它出的是哪个槽位（和 app.views.SHOT_JOB_SLOTS 一致）
JOB_KIND = {"image.keyframe": "first_frame", "image.continue_frame": "first_frame",
            "video.shot": "video"}


@dataclass
class KindTakes:
    takes: int = 0
    failed: int = 0
    # 选定的是第几条（按生成顺序，从 1 开始）；没选定为 None
    selected_index: int | None = None
    engines: dict[str, int] = field(default_factory=dict)   # 引擎 → 成功抽数


@dataclass
class ShotTakes:
    shot_id: int
    code: str
    first_frame: KindTakes = field(default_factory=KindTakes)
    video: KindTakes = field(default_factory=KindTakes)


@dataclass
class EngineTakes:
    engine: str
    kind: str
    takes: int = 0
    failed: int = 0


@dataclass
class EpisodeTakes:
    episode_id: int
    shots: list[ShotTakes]
    total_takes: int
    failed: int
    first_frame_takes: int
    video_takes: int
    shots_with_video: int
    # 有选定视频的镜头数 / 视频总抽数；还没抽过视频为 None
    usable_rate: float | None
    # 出完的镜头（有选定视频）平均每镜抽了几次（首帧 + 视频，含失败）；还没有出完的为 None
    avg_takes_per_finished_shot: float | None
    engines: list[EngineTakes]
    archived_takes: dict[str, int]      # 废弃镜头的抽卡：kind → 条数


def _engine_of_failed(s: Session, job_ids: list[int]) -> dict[int, str]:
    """失败任务 → 最后一次引擎调用的引擎名（一条查询）。"""
    if not job_ids:
        return {}
    eng = func.json_extract(JobTrace.data, "$.engine")
    out: dict[int, str] = {}
    for jid, e in s.execute(select(JobTrace.job_id, eng)
                            .where(JobTrace.job_id.in_(job_ids), JobTrace.kind == "engine")
                            .order_by(JobTrace.job_id, JobTrace.seq)):
        if e:
            out[jid] = str(e)
    return out


def episode_take_stats(s: Session, episode_id: int) -> EpisodeTakes:
    ep = s.get(Episode, episode_id)
    if ep is None:
        raise LookupError(f"找不到剧集 {episode_id}")
    project = s.get(Project, ep.project_id)
    shots = list(s.execute(select(Shot.id, Shot.code, Shot.image_engine, Shot.video_engine)
                           .where(Shot.episode_id == episode_id).order_by(Shot.order, Shot.id)))
    rows = {sid: ShotTakes(shot_id=sid, code=code) for sid, code, _, _ in shots}
    shot_ids = select(Shot.id).where(Shot.episode_id == episode_id).scalar_subquery()

    def slot_of(st: ShotTakes, kind: str) -> KindTakes:
        return st.first_frame if kind == "first_frame" else st.video

    engines: dict[tuple[str, str], EngineTakes] = {}

    def engine_row(engine: str, kind: str) -> EngineTakes:
        key = (engine, kind)
        if key not in engines:
            engines[key] = EngineTakes(engine=engine, kind=kind)
        return engines[key]

    # 成功的抽：按 镜头 × 槽位 × 引擎 计数
    for sid, kind, engine, n in s.execute(
            select(Slot.owner_id, Slot.kind, Candidate.engine, func.count(Candidate.id))
            .join(Candidate, Candidate.slot_id == Slot.id)
            .where(Slot.owner_type == "shot", Slot.kind.in_(KINDS), Slot.owner_id.in_(shot_ids))
            .group_by(Slot.owner_id, Slot.kind, Candidate.engine)):
        k = slot_of(rows[sid], kind)
        k.takes += n
        k.engines[engine or ""] = k.engines.get(engine or "", 0) + n
        engine_row(engine or "", kind).takes += n

    # 选定的是第几条：同一槽位里 id 不大于选定候选的候选数
    earlier = aliased(Candidate)
    for sid, kind, idx in s.execute(
            select(Slot.owner_id, Slot.kind, func.count(earlier.id))
            .join(Candidate, Candidate.id == Slot.selected_candidate_id)
            .join(earlier, (earlier.slot_id == Slot.id) & (earlier.id <= Candidate.id))
            .where(Slot.owner_type == "shot", Slot.kind.in_(KINDS), Slot.owner_id.in_(shot_ids),
                   Candidate.slot_id == Slot.id)
            .group_by(Slot.owner_id, Slot.kind)):
        slot_of(rows[sid], kind).selected_index = idx

    # 失败的任务
    failed = list(s.execute(select(Job.id, Job.owner_id, Job.type)
                            .where(Job.owner_type == "shot", Job.owner_id.in_(shot_ids),
                                   Job.type.in_(list(JOB_KIND)), Job.status == "failed")))
    traced = _engine_of_failed(s, [jid for jid, _, _ in failed])
    fallback = {sid: (img or (project.image_engine if project else None) or "",
                      vid or (project.video_engine if project else None) or "")
                for sid, _, img, vid in shots}
    for jid, sid, jtype in failed:
        kind = JOB_KIND[jtype]
        slot_of(rows[sid], kind).failed += 1
        engine = traced.get(jid) or fallback[sid][0 if kind == "first_frame" else 1]
        engine_row(engine, kind).failed += 1

    archived = {kind: n for kind, n in s.execute(
        select(ArchivedCandidate.kind, func.count(ArchivedCandidate.id))
        .where(ArchivedCandidate.episode_id == episode_id, ArchivedCandidate.kind.in_(KINDS))
        .group_by(ArchivedCandidate.kind))}

    shot_list = list(rows.values())
    ff_takes = sum(r.first_frame.takes for r in shot_list)
    v_takes = sum(r.video.takes for r in shot_list)
    n_failed = sum(r.first_frame.failed + r.video.failed for r in shot_list)
    finished = [r for r in shot_list if r.video.selected_index is not None]
    finished_takes = sum(r.first_frame.takes + r.first_frame.failed + r.video.takes + r.video.failed
                         for r in finished)
    return EpisodeTakes(
        episode_id=episode_id, shots=shot_list,
        total_takes=ff_takes + v_takes, failed=n_failed,
        first_frame_takes=ff_takes, video_takes=v_takes,
        shots_with_video=len(finished),
        usable_rate=round(len(finished) / v_takes, 4) if v_takes else None,
        avg_takes_per_finished_shot=round(finished_takes / len(finished), 2) if finished else None,
        engines=sorted(engines.values(), key=lambda e: (KINDS.index(e.kind), -(e.takes + e.failed),
                                                        e.engine)),
        archived_takes=archived,
    )
