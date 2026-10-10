"""把实体组装成 API 输出：镜头详情里带上每个槽位是否过期、为什么过期、进行中和失败的任务；
任务带上「是给谁做的」（项目 · 第几集 · 哪个镜头 / 哪个角色）和跳转地址。"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from urllib.parse import quote

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.schemas import (CandidateOut, EpisodeDetail, ExportOut, JobBrief, JobContext,
                                 SceneBlockOut, ShotDetail, SlotOut)
from backend.domain.models import Character, Episode, Job, Project, Shot
from backend.domain.refs import shot_refs
from backend.domain.slots import slots_for
from backend.pipeline.prompting import (keyframe_input_hash, keyframe_inputs, stale_reason,
                                        video_input_hash, video_inputs)
from backend.queue.manager import ACTIVE

# 镜头任务类型 → 它出的是哪个槽位
SHOT_JOB_SLOTS = {"image.keyframe": "first_frame", "video.shot": "video"}


def candidate_out(c) -> CandidateOut:
    return CandidateOut.model_validate(c).model_copy(update={"url": "/media/" + quote(c.file_path)})


def export_out(e) -> ExportOut:
    return ExportOut.model_validate(e).model_copy(update={"url": "/media/" + quote(e.file_path)})


def _max_refs(sv, kind: str, engine_name: str) -> int:
    try:
        return getattr(sv.engines, kind)(engine_name).caps.max_refs
    except KeyError:
        return 0


# ------------------------------------------------------------ 任务摘要
def first_line(text: str | None, n: int = 200) -> str | None:
    if not text:
        return None
    line = next((x.strip() for x in str(text).splitlines() if x.strip()), "")
    return line[:n] or None


def job_brief(j: Job, progress: Mapping[int, float] | None = None, *,
              failed: bool = False) -> JobBrief:
    return JobBrief(id=j.id, type=j.type, status=j.status, sheet=(j.payload or {}).get("sheet"),
                    progress=(progress or {}).get(j.id) if j.status == "running" else None,
                    error=first_line(j.error) if failed else None,
                    finished_at=j.finished_at if failed else None)


@dataclass
class OwnerJobs:
    """一个对象（镜头 / 角色）身上的任务：进行中的全部，失败 / 成功的每种类型只取最近一个。"""
    active: list[Job] = field(default_factory=list)
    last_failed: dict[str, Job] = field(default_factory=dict)
    last_done: dict[str, int] = field(default_factory=dict)

    def failure(self, job_type: str, selected_job_id: int | None = None) -> Job | None:
        """最近一次失败，且之后没有成功过、没有新排上的同类任务、也不早于选定候选的出图任务。"""
        f = self.last_failed.get(job_type)
        if f is None or self.last_done.get(job_type, 0) > f.id:
            return None
        if any(a.type == job_type and a.id > f.id for a in self.active):
            return None
        if selected_job_id is not None and selected_job_id > f.id:
            return None
        return f


def owner_jobs(s: Session, owner_type: str, owner_ids: Iterable[int],
               types: Iterable[str]) -> dict[int, OwnerJobs]:
    """一次查完一批对象的任务（剧集详情一次渲染所有镜头，不逐个镜头查）。"""
    ids = list(owner_ids)
    types = tuple(types)
    out: dict[int, OwnerJobs] = {i: OwnerJobs() for i in ids}
    if not ids:
        return out
    base = (Job.owner_type == owner_type, Job.owner_id.in_(ids), Job.type.in_(types))
    for j in s.scalars(select(Job).where(*base, Job.status.in_(ACTIVE)).order_by(Job.id)):
        out[j.owner_id].active.append(j)
    rows = s.execute(select(Job.owner_id, Job.type, Job.status, func.max(Job.id))
                     .where(*base, Job.status.in_(("failed", "done")))
                     .group_by(Job.owner_id, Job.type, Job.status)).all()
    failed_ids = []
    for oid, jtype, status, jid in rows:
        if status == "done":
            out[oid].last_done[jtype] = jid
        else:
            failed_ids.append(jid)
    if failed_ids:
        for j in s.scalars(select(Job).where(Job.id.in_(failed_ids))):
            out[j.owner_id].last_failed[j.type] = j
    return out


# ------------------------------------------------------------ 镜头
def shot_detail(s: Session, sv, shot, proj, style, *, jobs: OwnerJobs | None = None,
                progress: Mapping[int, float] | None = None) -> ShotDetail:
    slots = slots_for(s, "shot", shot.id)
    refs = shot_refs(s, shot)
    ie = shot.image_engine or proj.image_engine
    ff_refs = refs[:_max_refs(sv, "image", ie)]
    current: dict[str, str] = {
        "first_frame": keyframe_input_hash(shot, proj, style, ie,
                                           [r.candidate_id for r in ff_refs])}
    inputs: dict[str, dict] = {"first_frame": keyframe_inputs(shot, proj, style, ie, ff_refs)}
    ff = next((x for x in slots if x.kind == "first_frame"), None)
    if ff is not None and ff.selected_candidate_id:
        ve = shot.video_engine or proj.video_engine
        try:
            has_audio = sv.engines.video(ve).caps.has_audio
        except KeyError:
            has_audio = None
        if has_audio is not None:
            v_refs = refs[:_max_refs(sv, "video", ve)]
            current["video"] = video_input_hash(shot, proj, style, ff.selected_candidate_id, ve,
                                                has_audio, [r.candidate_id for r in v_refs])
            inputs["video"] = video_inputs(shot, proj, style, ff.selected_candidate_id, ve,
                                           has_audio, v_refs)
    outs = []
    selected_job: dict[str, int | None] = {}
    for sl in slots:
        sel = next((c for c in sl.candidates if c.id == sl.selected_candidate_id), None)
        stale = bool(sel is not None and sl.kind in current and sel.input_hash != current[sl.kind])
        reason = stale_reason((sel.params or {}).get("inputs"), inputs[sl.kind]) if stale else None
        newer = sum(1 for c in sl.candidates if c.id > sel.id) if sel is not None else 0
        selected_job[sl.kind] = sel.job_id if sel is not None else None
        outs.append(SlotOut(id=sl.id, kind=sl.kind, selected_candidate_id=sl.selected_candidate_id,
                            stale=stale, stale_reason=reason, new_candidates=newer,
                            candidates=[candidate_out(c) for c in sl.candidates]))
    if jobs is None:
        jobs = owner_jobs(s, "shot", [shot.id], SHOT_JOB_SLOTS)[shot.id]
    failures = [f for t, kind in SHOT_JOB_SLOTS.items()
                if (f := jobs.failure(t, selected_job.get(kind))) is not None]
    return ShotDetail.model_validate(shot).model_copy(update={
        "slots": outs,
        "active_jobs": [job_brief(j, progress) for j in jobs.active],
        "failures": [job_brief(f, failed=True) for f in sorted(failures, key=lambda x: x.id)]})


def episode_detail(s: Session, sv, ep, *, progress: Mapping[int, float] | None = None) -> EpisodeDetail:
    proj = ep.project
    style = proj.style_bible
    shots = list(ep.shots)
    jobs = owner_jobs(s, "shot", [sh.id for sh in shots], SHOT_JOB_SLOTS)
    return EpisodeDetail.model_validate(ep).model_copy(update={
        "scene_blocks": [SceneBlockOut.model_validate(b) for b in ep.scene_blocks],
        "shots": [shot_detail(s, sv, sh, proj, style, jobs=jobs[sh.id], progress=progress)
                  for sh in shots]})


# ------------------------------------------------------------ 任务上下文
EXPORT_JOB_PREFIXES = ("export.", "post.")


def _ep_url(slug: str, episode_id: int, page: str) -> str:
    return f"/projects/{quote(slug)}/episodes/{episode_id}/{page}"


def job_contexts(s: Session, jobs: list[dict]) -> dict[int, JobContext | None]:
    """任务 → 它是给谁做的。按 owner 批量查镜头 / 角色 / 剧集，不逐个任务查。"""
    want: dict[str, set[int]] = {"shot": set(), "character": set(), "episode": set()}
    project_ids: set[int] = set()
    for j in jobs:
        if j.get("owner_type") in want and j.get("owner_id") is not None:
            want[j["owner_type"]].add(j["owner_id"])
        if j.get("project_id") is not None:
            project_ids.add(j["project_id"])
    shots = {x.id: x for x in s.scalars(select(Shot).where(Shot.id.in_(want["shot"])))} \
        if want["shot"] else {}
    chars = {x.id: x for x in s.scalars(select(Character).where(Character.id.in_(want["character"])))} \
        if want["character"] else {}
    ep_ids = want["episode"] | {sh.episode_id for sh in shots.values()}
    eps = {x.id: x for x in s.scalars(select(Episode).where(Episode.id.in_(ep_ids)))} if ep_ids else {}
    project_ids |= {e.project_id for e in eps.values()} | {c.project_id for c in chars.values()}
    projs = {x.id: x for x in s.scalars(select(Project).where(Project.id.in_(project_ids)))} \
        if project_ids else {}
    # 角色卡跳到项目最近一集（编号最大）的选角页
    latest: dict[int, int] = {}
    char_projects = {c.project_id for c in chars.values()}
    if char_projects:
        for e in s.scalars(select(Episode).where(Episode.project_id.in_(char_projects))
                           .order_by(Episode.number, Episode.id)):
            latest[e.project_id] = e.id

    def episode_ctx(ep: Episode, page: str, **extra) -> JobContext | None:
        p = projs.get(ep.project_id)
        if p is None:
            return None
        return JobContext(label=f"{p.title} · 第{ep.number}集", url=_ep_url(p.slug, ep.id, page),
                          project_slug=p.slug, episode_id=ep.id, **extra)

    out: dict[int, JobContext | None] = {}
    for j in jobs:
        ot, oid, ctx = j.get("owner_type"), j.get("owner_id"), None
        if ot == "shot" and oid in shots:
            sh = shots[oid]
            ep = eps.get(sh.episode_id)
            base = episode_ctx(ep, "director") if ep else None
            if base is not None:
                ctx = base.model_copy(update={"label": f"{base.label} · {sh.code}",
                                              "url": f"{base.url}?shot={sh.id}", "shot_id": sh.id})
        elif ot == "character" and oid in chars:
            c = chars[oid]
            p = projs.get(c.project_id)
            if p is not None:
                ep_id = latest.get(p.id)
                url = _ep_url(p.slug, ep_id, "cast") if ep_id else f"/projects/{quote(p.slug)}"
                ctx = JobContext(label=f"{p.title} · {c.name}", url=url, project_slug=p.slug,
                                 episode_id=ep_id, character_id=c.id)
        elif ot == "episode" and oid in eps:
            page = "export" if str(j.get("type", "")).startswith(EXPORT_JOB_PREFIXES) else "script"
            ctx = episode_ctx(eps[oid], page)
        elif j.get("project_id") in projs:
            p = projs[j["project_id"]]
            ctx = JobContext(label=p.title, url=f"/projects/{quote(p.slug)}", project_slug=p.slug)
        out[j["id"]] = ctx
    return out


def with_context(s: Session, jobs: list[dict]) -> list[dict]:
    ctx = job_contexts(s, jobs)
    return [{**j, "context": ctx.get(j["id"])} for j in jobs]
