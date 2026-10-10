"""剧集与剧本段落。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy import func, select

from backend.app.deps import get_or_404, manager, project_by_slug, services
from backend.app.schemas import (EpisodeDetail, EpisodeIn, EpisodeOut, EpisodePatch, ExportOut,
                                 SceneBlockOut, SceneBlockPatch)
from backend.app.views import episode_detail, export_out
from backend.domain.models import Episode, Export, SceneBlock, Shot
from backend.domain.slots import delete_slots

router = APIRouter(prefix="/api", tags=["episodes"])


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
        delete_slots(s, "shot", s.scalars(select(Shot.id).where(Shot.episode_id == ep.id)).all())
        s.delete(ep)
    return Response(status_code=204)


@router.get("/episodes/{episode_id}/exports", response_model=list[ExportOut])
def list_exports(episode_id: int, request: Request):
    with services(request).db.session() as s:
        get_or_404(s, Episode, episode_id, "剧集")
        rows = s.scalars(select(Export).where(Export.episode_id == episode_id)
                         .order_by(Export.id.desc()))
        return [export_out(e) for e in rows]


@router.patch("/scene-blocks/{block_id}", response_model=SceneBlockOut)
def patch_scene_block(block_id: int, body: SceneBlockPatch, request: Request):
    with services(request).db.session() as s:
        blk = get_or_404(s, SceneBlock, block_id, "剧本段落")
        for k, v in body.model_dump(exclude_unset=True).items():
            setattr(blk, k, v)
        s.flush()
        return SceneBlockOut.model_validate(blk)
