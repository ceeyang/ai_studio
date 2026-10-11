"""续季 / 跨项目复用资产：列出一个项目的资产（导入对话框勾选用），从别的项目导入角色 / 场景 / 道具。"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Request
from sqlalchemy import func, select

from backend.app.deps import project_by_slug, services
from backend.app.schemas import AssetImportIn, AssetImportOut, AssetSummaryItem, AssetSummaryOut
from backend.domain.asset_import import import_assets
from backend.domain.models import Candidate, Character, Location, Prop, Slot

router = APIRouter(prefix="/api", tags=["asset-import"])

# 缩略图优先用哪个槽位
_THUMB = {"character": ("portrait", "turnaround_sheet_body", "turnaround_sheet_face"),
          "location": ("location_image",), "prop": ("prop_image",)}


def _summaries(s, owner_type: str, rows, detail) -> list[AssetSummaryItem]:
    ids = [r.id for r in rows]
    thumbs: dict[int, dict[str, str]] = {}
    counts: dict[int, int] = {}
    if ids:
        for owner_id, kind, path in s.execute(
                select(Slot.owner_id, Slot.kind, Candidate.file_path)
                .join(Candidate, Candidate.id == Slot.selected_candidate_id)
                .where(Slot.owner_type == owner_type, Slot.owner_id.in_(ids))):
            thumbs.setdefault(owner_id, {})[kind] = path
        counts = dict(s.execute(
            select(Slot.owner_id, func.count(Candidate.id))
            .join(Candidate, Candidate.slot_id == Slot.id)
            .where(Slot.owner_type == owner_type, Slot.owner_id.in_(ids))
            .group_by(Slot.owner_id)).all())
    out = []
    for r in rows:
        mine = thumbs.get(r.id, {})
        path = next((mine[k] for k in _THUMB[owner_type] if k in mine), None)
        out.append(AssetSummaryItem(id=r.id, name=r.name, detail=detail(r) or "",
                                    thumb_url="/media/" + quote(path) if path else None,
                                    image_count=counts.get(r.id, 0)))
    return out


@router.get("/projects/{slug}/asset-summary", response_model=AssetSummaryOut)
def asset_summary(slug: str, request: Request):
    """项目里全部角色、场景、道具（含分镜里已经不用的道具），各带一张选定图的缩略图。只读，不建道具行。"""
    with services(request).db.session() as s:
        p = project_by_slug(s, slug)
        chars = s.scalars(select(Character).where(Character.project_id == p.id)
                          .order_by(Character.id)).all()
        locs = s.scalars(select(Location).where(Location.project_id == p.id)
                         .order_by(Location.id)).all()
        props = s.scalars(select(Prop).where(Prop.project_id == p.id).order_by(Prop.id)).all()
        return AssetSummaryOut(
            characters=_summaries(s, "character", chars,
                                  lambda c: c.description or c.core_features),
            locations=_summaries(s, "location", locs,
                                 lambda x: " · ".join(v for v in (x.time_of_day, x.atmosphere) if v)),
            props=_summaries(s, "prop", props, lambda x: x.description),
            has_style=p.style_bible is not None)


@router.post("/projects/{slug}/import-assets", response_model=AssetImportOut)
def import_project_assets(slug: str, body: AssetImportIn, request: Request):
    """从源项目复制角色 / 场景 / 道具到本项目（续季）。媒体文件拷进本项目目录；重名的默认跳过，写进报告。"""
    sv = services(request)
    with sv.db.session() as s:
        target = project_by_slug(s, slug)
        source = project_by_slug(s, body.source)
        report = import_assets(
            s, sv.media, source=source, target=target, character_ids=body.character_ids,
            location_ids=body.location_ids, prop_ids=body.prop_ids, all_assets=body.all,
            kinds=body.kinds, on_conflict=body.on_conflict, rename_suffix=body.rename_suffix,
            candidates=body.candidates, with_style=body.with_style)
        return AssetImportOut.model_validate(report.to_dict())
