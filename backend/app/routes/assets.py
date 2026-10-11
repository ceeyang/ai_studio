"""资产选择器：列出本项目 / 本集已有的图片（GET /api/episodes/{id}/assets），镜头抽屉里拿来当构图参考
（POST /api/shots/{id}/refs/from-assets）或首帧（POST /api/shots/{id}/first-frame/from-asset）。
用的时候复制一份到镜头自己的媒体目录，原候选被删也不影响。"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Request

from backend.app.deps import get_or_404, services
from backend.app.schemas import AssetOut, FirstFrameFromAssetIn, ShotDetail, ShotRefsFromAssetsIn
from backend.app.views import shot_detail
from backend.domain.assets import add_first_frame_from_asset, add_shot_refs_from_assets, list_assets
from backend.domain.models import Episode, Shot

router = APIRouter(prefix="/api", tags=["assets"])


@router.get("/episodes/{episode_id}/assets", response_model=list[AssetOut])
def episode_assets(episode_id: int, request: Request, kind: str | None = None):
    """kind：character / location / prop / shot / archived / shot_ref；不给就全部。"""
    sv = services(request)
    with sv.db.session() as s:
        ep = get_or_404(s, Episode, episode_id, "剧集")
        out = []
        for a in list_assets(s, sv.media, ep, kind):
            url = "/media/" + quote(a.file_path)
            out.append(AssetOut(id=a.id, kind=a.kind, slot_kind=a.slot_kind, media=a.media,
                                label=a.label, source=a.source, url=url, thumb_url=url,
                                width=a.width, height=a.height, selected=a.selected,
                                shot_id=a.shot_id, owner_id=a.owner_id, engine=a.engine,
                                created_at=a.created_at))
        return out


def _shot_ctx(s, shot_id: int):
    shot = get_or_404(s, Shot, shot_id, "镜头")
    ep = s.get(Episode, shot.episode_id)
    return shot, ep.project


@router.post("/shots/{shot_id}/refs/from-assets", response_model=ShotDetail)
def shot_refs_from_assets(shot_id: int, body: ShotRefsFromAssetsIn, request: Request):
    """选中的资产复制成这一镜的构图参考图（每镜最多 2 张，和上传的共用名额）。"""
    sv = services(request)
    with sv.db.session() as s:
        shot, proj = _shot_ctx(s, shot_id)
        add_shot_refs_from_assets(s, sv, shot, proj.slug, proj.id,
                                  [(a.id, a.frame) for a in body.assets])
        return shot_detail(s, sv, shot, proj, proj.style_bible)


@router.post("/shots/{shot_id}/first-frame/from-asset", response_model=ShotDetail)
def first_frame_from_asset(shot_id: int, body: FirstFrameFromAssetIn, request: Request):
    """拿一张已有的图当这一镜的首帧候选（来源记成 asset），select 时设为选定；crop 时居中裁成项目画幅。"""
    sv = services(request)
    with sv.db.session() as s:
        shot, proj = _shot_ctx(s, shot_id)
        add_first_frame_from_asset(s, sv, shot, proj.slug, proj.id, proj.aspect, body.id,
                                   body.frame, crop=body.crop, select=body.select)
        return shot_detail(s, sv, shot, proj, proj.style_bible)
