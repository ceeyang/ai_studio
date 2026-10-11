"""项目、角色、场景、风格。"""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy import func, select

from backend.app.deps import (check_engines, get_or_404, manager, project_by_slug, read_upload,
                              services)
from backend.app.schemas import (AppearanceRefOut, CharacterIn, CharacterOut, CharacterPatch,
                                 EndCardIO, EpisodeStats, PortraitPathIn, ProjectIn, ProjectOut,
                                 ProjectOverview, ProjectPatch, StyleIO, StylePresetOut,
                                 TurnaroundCellOut,
                                 TurnaroundOut, TurnaroundSheetOut)
from backend.domain.characters import (APPEARANCE_REF, portrait_ext, portrait_of,
                                       set_portrait)
from backend.domain.style_presets import PRESETS, get_preset, recommended
from backend.domain.settings import default_image_engine, default_video_engine, sheet_engine
from backend.domain.turnaround import (DEFAULT_SHEET_ENGINE, SHEETS, VIEW_KEYS, VIEW_LABELS,
                                       VIEW_SHEET, cell_state, crop_method, sheet_slot_kind,
                                       sheet_state)
from backend.domain.models import (Candidate, Character, Episode, Export, Job, Project,
                                   Shot, Slot, StyleBible)
from backend.app.views import candidate_out, job_brief, owner_jobs
from backend.domain.slots import delete_slots, slots_for
from backend.queue.manager import ACTIVE
from backend.queue.errors import JobError

router = APIRouter(prefix="/api", tags=["projects"])
SLUG_RE = re.compile(r"^[\w\-]{1,64}$")


def make_slug(title: str) -> str:
    s = re.sub(r"\s+", "-", title.strip())
    s = re.sub(r"[^\w\-]", "", s)[:64].strip("-")
    return s or "project"


@router.get("/projects", response_model=list[ProjectOut])
def list_projects(request: Request, archived: bool = False):
    """默认不含已归档的项目；?archived=1 连归档的一起全部返回。"""
    with services(request).db.session() as s:
        q = select(Project).order_by(Project.id)
        if not archived:
            q = q.where(Project.archived.is_(False))
        return [ProjectOut.model_validate(p) for p in s.scalars(q)]


@router.get("/style-presets", response_model=list[StylePresetOut])
def list_style_presets(request: Request):
    """风格预设，带上推荐引擎里第一个可用的（新建项目对话框预选，用户可以改）。"""
    sv = services(request)
    images, videos = sv.engines.names("image"), sv.engines.names("video")
    return [StylePresetOut(**p.public(), image_engine=recommended(images, p.image_engines),
                           video_engine=recommended(videos, p.video_engines),
                           portrait_engine=recommended(images, p.portrait_engines))
            for p in PRESETS.values()]


@router.post("/projects", status_code=201, response_model=ProjectOut)
def create_project(body: ProjectIn, request: Request):
    sv = services(request)
    slug = body.slug or make_slug(body.title)
    if not SLUG_RE.match(slug):
        raise JobError("slug 只能包含文字、数字、下划线和短横线，最长 64 个字符")
    check_engines(sv, body.image_engine, body.video_engine)
    data = body.model_dump(exclude={"slug"})
    if body.negative is None:
        data["negative"] = get_preset(body.style_preset).negative
    data["image_engine"] = body.image_engine or default_image_engine(sv)
    data["video_engine"] = body.video_engine or default_video_engine(sv)
    with sv.db.session() as s:
        if s.scalar(select(Project).where(Project.slug == slug)):
            raise HTTPException(409, f"项目 {slug} 已存在")
        p = Project(slug=slug, **data)
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
        new_preset = data.get("style_preset")
        if new_preset and new_preset != p.style_preset and "negative" not in data:
            # 负面词没自己改过（还是旧预设的默认值或空）就换成新预设的默认值
            if (p.negative or "").strip() in ("", get_preset(p.style_preset).negative):
                p.negative = get_preset(new_preset).negative
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
        delete_slots(s, "location", [x.id for x in p.locations])
        delete_slots(s, "episode", [e.id for e in p.episodes])
        s.delete(p)
    return Response(status_code=204)


CHARACTER_JOB_TYPES = ("image.portrait", "character.suggest", "character.turnaround_look",
                       "image.turnaround", "character.from_photo")


def _url(c) -> str | None:
    return "/media/" + quote(c.file_path) if c else None


def turnaround_out(s, c: Character, slots: dict, sv=None) -> TurnaroundOut:
    sheets = []
    for key, spec in SHEETS.items():
        slot = slots.get(sheet_slot_kind(key))
        sel, is_stale = sheet_state(s, c, key)
        sheets.append(TurnaroundSheetOut(
            sheet=key, label=spec.label, views=list(spec.views),
            selected_candidate_id=sel.id if sel else None, url=_url(sel), stale=is_stale,
            crop_method=crop_method(s, c, key),
            candidates=[candidate_out(x) for x in slot.candidates] if slot else []))
    cells = []
    for view in VIEW_KEYS:
        sel, is_stale = cell_state(s, c, view)
        cells.append(TurnaroundCellOut(view=view, label=VIEW_LABELS[view], sheet=VIEW_SHEET[view],
                                       selected_candidate_id=sel.id if sel else None,
                                       url=_url(sel), stale=is_stale))
    # 不选引擎时实际会用的（设置页三视图这一步 → 有 H3 用 H3 → 项目的图像引擎）
    default = sheet_engine(sv, c.project.image_engine) if sv is not None else DEFAULT_SHEET_ENGINE
    return TurnaroundOut(look=c.turnaround_look or "", default_engine=default,
                         sheets=sheets, cells=cells)


def appearance_ref_out(slot) -> AppearanceRefOut | None:
    if slot is None or slot.selected_candidate_id is None:
        return None
    sel = next((x for x in slot.candidates if x.id == slot.selected_candidate_id), None)
    if sel is None:
        return None
    look = (sel.params or {}).get("look") or {}
    return AppearanceRefOut(candidate_id=sel.id, url=_url(sel),
                            visual_prompt=look.get("visual_prompt") or None,
                            core_features=look.get("core_features") or None)


def character_out(s, c: Character, progress=None, sv=None) -> CharacterOut:
    pc = portrait_of(s, c.id)
    slots = {x.kind: x for x in slots_for(s, "character", c.id)}
    slot = slots.get("portrait")
    jobs = s.scalars(select(Job).where(Job.owner_type == "character", Job.owner_id == c.id,
                                       Job.type.in_(CHARACTER_JOB_TYPES),
                                       Job.status.in_(ACTIVE)).order_by(Job.id)).all()
    # 最近一次抽卡失败（之后成功过、或又排上了新的抽卡就不提示）
    portrait_jobs = owner_jobs(s, "character", [c.id], ("image.portrait",))[c.id]
    failed = portrait_jobs.failure("image.portrait")
    if failed is not None and slot is not None and slot.candidates:
        # 失败之后又有了新定妆照（用户自己上传的、或别的途径加的）：失败提示作废
        newest = max(x.created_at for x in slot.candidates)
        if newest > (failed.finished_at or failed.created_at):
            failed = None
    return CharacterOut.model_validate(c).model_copy(update={
        "portrait_candidate_id": pc.id if pc else None,
        "portrait_url": "/media/" + quote(pc.file_path) if pc else None,
        "portraits": [candidate_out(x) for x in slot.candidates] if slot else [],
        "design_options": list(c.design_options or []),
        "active_jobs": [job_brief(j, progress) for j in jobs],
        "last_failure": job_brief(failed, failed=True) if failed else None,
        "turnaround": turnaround_out(s, c, slots, sv),
        "appearance_ref": appearance_ref_out(slots.get(APPEARANCE_REF))})


@router.get("/projects/{slug}/characters", response_model=list[CharacterOut])
def list_characters(slug: str, request: Request):
    with services(request).db.session() as s:
        return [character_out(s, c, manager(request).progress, sv=services(request))
                for c in project_by_slug(s, slug).characters]


@router.post("/projects/{slug}/characters", status_code=201, response_model=CharacterOut)
def create_character(slug: str, body: CharacterIn, request: Request):
    with services(request).db.session() as s:
        p = project_by_slug(s, slug)
        if any(c.name == body.name for c in p.characters):
            raise HTTPException(409, f"角色 {body.name} 已存在")
        c = Character(project_id=p.id, **body.model_dump())
        s.add(c)
        s.flush()
        return character_out(s, c, sv=services(request))


@router.patch("/characters/{char_id}", response_model=CharacterOut)
def patch_character(char_id: int, body: CharacterPatch, request: Request):
    with services(request).db.session() as s:
        c = get_or_404(s, Character, char_id, "角色")
        for k, v in body.model_dump(exclude_unset=True).items():
            setattr(c, k, v)
        s.flush()
        return character_out(s, c, sv=services(request))


async def read_image_upload(request: Request) -> tuple[bytes, str]:
    """multipart 上传的 file 字段，或 JSON {"path": 本机图片路径}（只读图片扩展名）。"""
    if request.headers.get("content-type", "").startswith("multipart/form-data"):
        return await read_upload(request)
    body = PortraitPathIn.model_validate(await request.json())
    src = Path(body.path)
    portrait_ext(src.name)  # 先校验扩展名，只读图片
    if not src.is_file():
        raise LookupError(f"找不到文件：{body.path}")
    return src.read_bytes(), src.name


@router.post("/characters/{char_id}/portrait", response_model=CharacterOut)
async def upload_portrait(char_id: int, request: Request):
    """设定妆照：multipart 上传 file 字段，或 JSON {"path": 本机图片路径}。新图成为选定候选。"""
    data, name = await read_image_upload(request)
    sv = services(request)
    with sv.db.session() as s:
        c = get_or_404(s, Character, char_id, "角色")
        set_portrait(s, sv.media, c, data, name)
        return character_out(s, c, sv=services(request))


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
