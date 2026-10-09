"""项目、角色、场景、风格。"""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy import func, select

from backend.app.deps import check_engines, get_or_404, project_by_slug, services
from backend.app.schemas import (CharacterIn, CharacterOut, CharacterPatch, EndCardIO,
                                 EpisodeStats, LocationOut, LocationPatch, PortraitPathIn, ProjectIn, ProjectOut,
                                 ProjectOverview, ProjectPatch, StyleIO)
from backend.domain.characters import portrait_ext, portrait_of, set_portrait
from backend.domain.models import (Candidate, Character, Episode, Export, Location, Project, Shot,
                                   Slot, StyleBible)
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


@router.get("/projects/{slug}/overview", response_model=ProjectOverview)
def project_overview(slug: str, request: Request):
    """项目中心卡片用：每集镜头数、成片数和封面（按镜头顺序第一张选定的首帧）。"""
    with services(request).db.session() as s:
        p = project_by_slug(s, slug)
        eps = list(p.episodes)
        ep_ids = [e.id for e in eps]
        shots = dict(s.execute(select(Shot.episode_id, func.count(Shot.id))
                               .where(Shot.episode_id.in_(ep_ids)).group_by(Shot.episode_id)).all())
        exports = dict(s.execute(select(Export.episode_id, func.count(Export.id))
                                 .where(Export.episode_id.in_(ep_ids))
                                 .group_by(Export.episode_id)).all())
        covers: dict[int, str] = {}
        rows = s.execute(
            select(Shot.episode_id, Candidate.file_path)
            .join(Slot, (Slot.owner_type == "shot") & (Slot.owner_id == Shot.id)
                  & (Slot.kind == "first_frame"))
            .join(Candidate, Candidate.id == Slot.selected_candidate_id)
            .where(Shot.episode_id.in_(ep_ids))
            .order_by(Shot.episode_id, Shot.order))
        for ep_id, path in rows:
            covers.setdefault(ep_id, "/media/" + quote(path))
        stats = [EpisodeStats(id=e.id, number=e.number, title=e.title, status=e.status,
                              shot_count=shots.get(e.id, 0), export_count=exports.get(e.id, 0),
                              cover_url=covers.get(e.id)) for e in eps]
        cover = next((x.cover_url for x in reversed(stats) if x.cover_url), None)
        return ProjectOverview(episode_count=len(stats), cover_url=cover, episodes=stats)


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
        delete_slots(s, "character", [c.id for c in p.characters])
        s.delete(p)
    return Response(status_code=204)


def character_out(s, c: Character) -> CharacterOut:
    pc = portrait_of(s, c.id)
    return CharacterOut.model_validate(c).model_copy(update={
        "portrait_candidate_id": pc.id if pc else None,
        "portrait_url": "/media/" + quote(pc.file_path) if pc else None})


@router.get("/projects/{slug}/characters", response_model=list[CharacterOut])
def list_characters(slug: str, request: Request):
    with services(request).db.session() as s:
        return [character_out(s, c) for c in project_by_slug(s, slug).characters]


@router.post("/projects/{slug}/characters", status_code=201, response_model=CharacterOut)
def create_character(slug: str, body: CharacterIn, request: Request):
    with services(request).db.session() as s:
        p = project_by_slug(s, slug)
        if any(c.name == body.name for c in p.characters):
            raise HTTPException(409, f"角色 {body.name} 已存在")
        c = Character(project_id=p.id, **body.model_dump())
        s.add(c)
        s.flush()
        return character_out(s, c)


@router.patch("/characters/{char_id}", response_model=CharacterOut)
def patch_character(char_id: int, body: CharacterPatch, request: Request):
    with services(request).db.session() as s:
        c = get_or_404(s, Character, char_id, "角色")
        for k, v in body.model_dump(exclude_unset=True).items():
            setattr(c, k, v)
        s.flush()
        return character_out(s, c)


@router.post("/characters/{char_id}/portrait", response_model=CharacterOut)
async def upload_portrait(char_id: int, request: Request):
    """设定妆照：multipart 上传 file 字段，或 JSON {"path": 本机图片路径}。新图成为选定候选。"""
    if request.headers.get("content-type", "").startswith("multipart/form-data"):
        form = await request.form()
        up = form.get("file")
        if up is None or isinstance(up, str):
            raise JobError("缺少上传文件字段 file")
        data, name = await up.read(), up.filename or ""
    else:
        body = PortraitPathIn.model_validate(await request.json())
        src = Path(body.path)
        portrait_ext(src.name)  # 先校验扩展名，只读图片
        if not src.is_file():
            raise LookupError(f"找不到文件：{body.path}")
        data, name = src.read_bytes(), src.name
    sv = services(request)
    with sv.db.session() as s:
        c = get_or_404(s, Character, char_id, "角色")
        set_portrait(s, sv.media, c, data, name)
        return character_out(s, c)


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


@router.get("/projects/{slug}/end-card", response_model=EndCardIO | None)
def get_end_card(slug: str, request: Request):
    with services(request).db.session() as s:
        card = project_by_slug(s, slug).end_card
        return EndCardIO.model_validate(card) if card else None


@router.put("/projects/{slug}/end-card", response_model=EndCardIO)
def put_end_card(slug: str, body: EndCardIO, request: Request):
    """片尾卡（三语店名、地址、营业时间、联系方式），导出勾选"片尾卡"时接在成片末尾。"""
    with services(request).db.session() as s:
        project_by_slug(s, slug).end_card = body.model_dump()
        return body
