"""项目、角色、场景、风格。"""
from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy import select

from backend.app.deps import check_engines, get_or_404, project_by_slug, services
from backend.app.schemas import (CharacterOut, CharacterPatch, LocationOut, LocationPatch,
                                 ProjectIn, ProjectOut, ProjectPatch, StyleIO)
from backend.domain.models import Character, Episode, Location, Project, Shot, StyleBible
from backend.domain.slots import delete_slots
from backend.queue.errors import JobError

router = APIRouter(prefix="/api", tags=["projects"])
SLUG_RE = re.compile(r"^[\w\-]{1,64}$")


def make_slug(title: str) -> str:
    s = re.sub(r"\s+", "-", title.strip())
    s = re.sub(r"[^\w\-]", "", s)[:64].strip("-")
    return s or "project"


@router.get("/projects", response_model=list[ProjectOut])
def list_projects(request: Request):
    with services(request).db.session() as s:
        return [ProjectOut.model_validate(p) for p in s.scalars(select(Project).order_by(Project.id))]


@router.post("/projects", status_code=201, response_model=ProjectOut)
def create_project(body: ProjectIn, request: Request):
    sv = services(request)
    slug = body.slug or make_slug(body.title)
    if not SLUG_RE.match(slug):
        raise JobError("slug 只能包含文字、数字、下划线和短横线，最长 64 个字符")
    check_engines(sv, body.image_engine, body.video_engine)
    with sv.db.session() as s:
        if s.scalar(select(Project).where(Project.slug == slug)):
            raise HTTPException(409, f"项目 {slug} 已存在")
        p = Project(slug=slug, **body.model_dump(exclude={"slug"}))
        s.add(p)
        s.flush()
        out = ProjectOut.model_validate(p)
    sv.media.project_dir(slug).mkdir(parents=True, exist_ok=True)
    return out


@router.get("/projects/{slug}", response_model=ProjectOut)
def get_project(slug: str, request: Request):
    with services(request).db.session() as s:
        return ProjectOut.model_validate(project_by_slug(s, slug))


@router.patch("/projects/{slug}", response_model=ProjectOut)
def patch_project(slug: str, body: ProjectPatch, request: Request):
    sv = services(request)
    data = body.model_dump(exclude_unset=True)
    check_engines(sv, data.get("image_engine"), data.get("video_engine"))
    with sv.db.session() as s:
        p = project_by_slug(s, slug)
        for k, v in data.items():
            if v is not None:
                setattr(p, k, v)
        s.flush()
        return ProjectOut.model_validate(p)


@router.delete("/projects/{slug}", status_code=204)
def delete_project(slug: str, request: Request):
    with services(request).db.session() as s:
        p = project_by_slug(s, slug)
        delete_slots(s, "shot", s.scalars(select(Shot.id).join(Episode, Shot.episode_id == Episode.id)
                                          .where(Episode.project_id == p.id)).all())
        s.delete(p)
    return Response(status_code=204)


@router.get("/projects/{slug}/characters", response_model=list[CharacterOut])
def list_characters(slug: str, request: Request):
    with services(request).db.session() as s:
        return [CharacterOut.model_validate(c) for c in project_by_slug(s, slug).characters]


@router.patch("/characters/{char_id}", response_model=CharacterOut)
def patch_character(char_id: int, body: CharacterPatch, request: Request):
    with services(request).db.session() as s:
        c = get_or_404(s, Character, char_id, "角色")
        for k, v in body.model_dump(exclude_unset=True).items():
            setattr(c, k, v)
        s.flush()
        return CharacterOut.model_validate(c)


@router.get("/projects/{slug}/locations", response_model=list[LocationOut])
def list_locations(slug: str, request: Request):
    with services(request).db.session() as s:
        return [LocationOut.model_validate(l) for l in project_by_slug(s, slug).locations]


@router.patch("/locations/{loc_id}", response_model=LocationOut)
def patch_location(loc_id: int, body: LocationPatch, request: Request):
    with services(request).db.session() as s:
        loc = get_or_404(s, Location, loc_id, "场景")
        for k, v in body.model_dump(exclude_unset=True).items():
            setattr(loc, k, v)
        s.flush()
        return LocationOut.model_validate(loc)


@router.get("/projects/{slug}/style", response_model=StyleIO | None)
def get_style(slug: str, request: Request):
    with services(request).db.session() as s:
        sb = project_by_slug(s, slug).style_bible
        return StyleIO.model_validate(sb) if sb else None


@router.put("/projects/{slug}/style", response_model=StyleIO)
def put_style(slug: str, body: StyleIO, request: Request):
    with services(request).db.session() as s:
        p = project_by_slug(s, slug)
        sb = p.style_bible
        if sb is None:
            sb = StyleBible()
            s.add(sb)
            p.style_bible = sb
        for k, v in body.model_dump().items():
            setattr(sb, k, v)
        s.flush()
        return StyleIO.model_validate(sb)
