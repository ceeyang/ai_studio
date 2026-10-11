"""剧集与剧本段落。"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy import func, select, update

from backend.app.deps import get_or_404, manager, project_by_slug, read_upload, services
from backend.app.schemas import (BgmOut, EpisodeDetail, EpisodeIdeaIn, EpisodeIdeaOut, EpisodeIn,
                                 EpisodeOut, EpisodePatch, ExportOut, ExportPatch, RunOut,
                                 SceneBlockOut, SceneBlockPatch, ScriptReviewOut, TakeStatsOut,
                                 EpisodeEngineAdviceOut)
from backend.app.views import candidate_out, episode_detail, export_out
from backend.domain.models import Episode, Export, Job, SceneBlock, Shot
from backend.domain.music import bgm_of, clear_bgm, set_bgm
from backend.domain.slots import delete_slots
from backend.domain.take_stats import episode_take_stats
from backend.pipeline.episode_idea import IdeaFailed, write_episode_idea
from backend.pipeline.script_review import TASK as REVIEW_TASK
from backend.pipeline.script_review import rules_view, stored_review
from backend.queue.errors import JobError
from backend.queue.manager import ACTIVE

router = APIRouter(prefix="/api", tags=["episodes"])
log = logging.getLogger(__name__)


@router.get("/projects/{slug}/episodes", response_model=list[EpisodeOut])
def list_episodes(slug: str, request: Request):
    with services(request).db.session() as s:
        return [EpisodeOut.model_validate(e) for e in project_by_slug(s, slug).episodes]


@router.post("/projects/{slug}/episodes", status_code=201, response_model=EpisodeOut)
def create_episode(slug: str, body: EpisodeIn, request: Request):
    with services(request).db.session() as s:
        p = project_by_slug(s, slug)
        number = body.number or (s.scalar(select(func.max(Episode.number))
                                          .where(Episode.project_id == p.id)) or 0) + 1
        if s.scalar(select(Episode).where(Episode.project_id == p.id, Episode.number == number)):
            raise HTTPException(409, f"第 {number} 集已存在")
        ep = Episode(project_id=p.id, number=number, title=body.title,
                     source_text=body.source_text, target_seconds=body.target_seconds)
        s.add(ep)
        s.flush()
        return EpisodeOut.model_validate(ep)


@router.post("/projects/{slug}/episode-idea", response_model=EpisodeIdeaOut)
def episode_idea(slug: str, body: EpisodeIdeaIn, request: Request):
    """新建剧集对话框的「AI 写梗概 / AI 续写」：同步调用 LLM，返回 {text, mode}。
    mode=continue 时 text 只是接在后面的新内容；new / expand 是完整梗概。什么都不存。"""
    try:
        r = write_episode_idea(services(request), slug, title=body.title,
                               target_seconds=body.target_seconds, number=body.number, text=body.text)
    except IdeaFailed as e:
        raise HTTPException(502, str(e)) from None
    return EpisodeIdeaOut(text=r.text, mode=r.mode)


@router.get("/projects/{slug}/episodes/by-number/{number}", response_model=EpisodeDetail)
def get_episode_by_number(slug: str, number: int, request: Request):
    sv = services(request)
    with sv.db.session() as s:
        p = project_by_slug(s, slug)
        ep = s.scalar(select(Episode).where(Episode.project_id == p.id, Episode.number == number))
        if ep is None:
            raise LookupError(f"项目 {slug} 没有第 {number} 集")
        return episode_detail(s, sv, ep, progress=manager(request).progress)


@router.get("/episodes/{episode_id}", response_model=EpisodeDetail)
def get_episode(episode_id: int, request: Request):
    sv = services(request)
    with sv.db.session() as s:
        return episode_detail(s, sv, get_or_404(s, Episode, episode_id, "剧集"),
                              progress=manager(request).progress)


@router.patch("/episodes/{episode_id}", response_model=EpisodeOut)
def patch_episode(episode_id: int, body: EpisodePatch, request: Request):
    with services(request).db.session() as s:
        ep = get_or_404(s, Episode, episode_id, "剧集")
        for k, v in body.model_dump(exclude_unset=True).items():
            if v is not None:
                setattr(ep, k, v)
        s.flush()
        return EpisodeOut.model_validate(ep)


@router.delete("/episodes/{episode_id}", status_code=204)
def delete_episode(episode_id: int, request: Request):
    with services(request).db.session() as s:
        ep = get_or_404(s, Episode, episode_id, "剧集")
        shot_ids = s.scalars(select(Shot.id).where(Shot.episode_id == ep.id)).all()
        active = s.scalar(select(func.count()).select_from(Job).where(
            Job.status.in_(ACTIVE),
            ((Job.owner_type == "episode") & (Job.owner_id == ep.id))
            | ((Job.owner_type == "shot") & (Job.owner_id.in_(shot_ids)))))
        if active:
            raise HTTPException(409, "这一集还有排队或执行中的任务，先取消任务再删除")
        number, project_id = ep.number, ep.project_id
        delete_slots(s, "shot", shot_ids)
        delete_slots(s, "episode", [ep.id])   # 配乐
        # exports 表对 episode_id 有外键 ON DELETE CASCADE，删集时自动一并删掉，成片文件留在磁盘上
        s.delete(ep)
    log.info("删除第 %s 集（项目 %s，episode_id=%s），连带 %d 个镜头",
             number, project_id, episode_id, len(shot_ids))
    return Response(status_code=204)


@router.get("/episodes/{episode_id}/exports", response_model=list[ExportOut])
def list_exports(episode_id: int, request: Request):
    with services(request).db.session() as s:
        get_or_404(s, Episode, episode_id, "剧集")
        rows = s.scalars(select(Export).where(Export.episode_id == episode_id)
                         .order_by(Export.is_final.desc(), Export.id.desc()))
        return [export_out(e) for e in rows]


@router.patch("/exports/{export_id}", response_model=ExportOut)
def patch_export(export_id: int, body: ExportPatch, request: Request):
    """定稿：同一集只允许一个成片是 is_final，定下一个会自动取消同集其它定稿。"""
    with services(request).db.session() as s:
        exp = get_or_404(s, Export, export_id, "成片")
        if body.is_final:
            s.execute(update(Export).where(Export.episode_id == exp.episode_id,
                                           Export.id != exp.id).values(is_final=False))
        exp.is_final = body.is_final
        s.flush()
        return export_out(exp)


@router.patch("/scene-blocks/{block_id}", response_model=SceneBlockOut)
def patch_scene_block(block_id: int, body: SceneBlockPatch, request: Request):
    with services(request).db.session() as s:
        blk = get_or_404(s, SceneBlock, block_id, "剧本段落")
        for k, v in body.model_dump(exclude_unset=True).items():
            setattr(blk, k, v)
        s.flush()
        return SceneBlockOut.model_validate(blk)


@router.get("/episodes/{episode_id}/take-stats", response_model=TakeStatsOut)
def get_take_stats(episode_id: int, request: Request):
    """抽卡统计：每个镜头首帧 / 视频抽了几次（成功 + 失败）、选的第几条，一集的可用率和按引擎的成功 / 失败。"""
    with services(request).db.session() as s:
        get_or_404(s, Episode, episode_id, "剧集")
        return TakeStatsOut.model_validate(episode_take_stats(s, episode_id))


@router.get("/episodes/{episode_id}/engine-advice", response_model=EpisodeEngineAdviceOut)
def get_engine_advice(episode_id: int, request: Request):
    """一集所有镜头的视频引擎建议（一次查完）；switch_count = 建议换引擎的镜头数。只是建议，不会自动换。"""
    from backend.app.routes.system import video_engine_infos
    from backend.domain.engine_advice import advise_episode

    sv = services(request)
    infos = video_engine_infos(sv)
    with sv.db.session() as s:
        ep = get_or_404(s, Episode, episode_id, "剧集")
        shots = s.scalars(select(Shot).where(Shot.episode_id == episode_id).order_by(Shot.order, Shot.id)).all()
        adv = advise_episode(episode_id, shots, infos, ep.project.video_engine)
        return EpisodeEngineAdviceOut.model_validate(adv)


# ------------------------------------------------------------ 剧本体检
def _review_job(s, episode_id: int):
    return s.scalar(select(Job).where(Job.type == REVIEW_TASK, Job.owner_type == "episode",
                                      Job.owner_id == episode_id).order_by(Job.id.desc()).limit(1))


@router.get("/episodes/{episode_id}/script-review", response_model=ScriptReviewOut)
def get_script_review(episode_id: int, request: Request):
    """剧本体检：规则检查按现在的分镜现算（不调 LLM）；review 是最近一次 LLM 体检的打分和改写建议
    （分镜改过之后 stale=true）；job 是最近一次体检任务（排队、运行中、失败时界面要显示）。"""
    with services(request).db.session() as s:
        ep = get_or_404(s, Episode, episode_id, "剧集")
        j = _review_job(s, episode_id)
        return ScriptReviewOut(
            episode_id=ep.id, rules=rules_view(ep), review=stored_review(ep),
            job=None if j is None else {"id": j.id, "status": j.status, "error": j.error})


@router.post("/episodes/{episode_id}/script-review", response_model=RunOut)
def run_script_review(episode_id: int, request: Request):
    """提交 LLM 剧本体检（script.review）。已经有排队 / 运行中的体检时不重复提交，返回那一个。"""
    with services(request).db.session() as s:
        ep = get_or_404(s, Episode, episode_id, "剧集")
        if not ep.shots:
            raise JobError("这一集还没有分镜，先生成分镜再做剧本体检")
        project_id = ep.project_id
        j = _review_job(s, episode_id)
        if j is not None and j.status in ACTIVE:
            return RunOut(job_ids=[j.id], notes=["这一集的剧本体检已经在排队或运行中"])
    jid = manager(request).submit(REVIEW_TASK, {"episode_id": episode_id}, project_id=project_id,
                                  owner_type="episode", owner_id=episode_id)
    return RunOut(job_ids=[jid])


# ------------------------------------------------------------ 配乐
def _bgm_out(c) -> BgmOut | None:
    if c is None:
        return None
    return BgmOut(candidate_id=c.id, url=candidate_out(c).url,
                  filename=str((c.params or {}).get("source") or c.file_path.rsplit("/", 1)[-1]),
                  created_at=c.created_at)


@router.get("/episodes/{episode_id}/bgm", response_model=BgmOut | None)
def get_bgm(episode_id: int, request: Request):
    with services(request).db.session() as s:
        get_or_404(s, Episode, episode_id, "剧集")
        return _bgm_out(bgm_of(s, episode_id))


@router.post("/episodes/{episode_id}/bgm", response_model=BgmOut)
async def upload_bgm(episode_id: int, request: Request):
    """上传 / 替换这一集的配乐（multipart 的 file 字段：mp3 / wav / m4a / aac / ogg / flac）。"""
    data, name = await read_upload(request)
    sv = services(request)
    with sv.db.session() as s:
        ep = get_or_404(s, Episode, episode_id, "剧集")
        return _bgm_out(set_bgm(s, sv.media, ep, data, name))


@router.delete("/episodes/{episode_id}/bgm", status_code=204)
def delete_bgm(episode_id: int, request: Request):
    """去掉配乐（文件留在磁盘上）。之后导出不能再勾「配乐」。"""
    with services(request).db.session() as s:
        get_or_404(s, Episode, episode_id, "剧集")
        clear_bgm(s, episode_id)
    return Response(status_code=204)
