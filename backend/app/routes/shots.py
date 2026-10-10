"""镜头：增删改、排序、拆分。"""
from __future__ import annotations

from fastapi import APIRouter, Request, Response

from backend.app.deps import check_engines, get_or_404, manager, services
from backend.app.schemas import ReorderIn, ShotDetail, ShotFields, ShotIn, ShotOut
from backend.app.views import shot_detail
from backend.domain.models import Episode, Shot
from backend.domain.shots import renumber_shots
from backend.domain.slots import delete_slots

router = APIRouter(prefix="/api", tags=["shots"])

COPY_FIELDS = ("scene_block_id", "shot_size", "camera_move", "action", "cast", "props",
               "image_prompt", "motion_prompt", "audio_prompt", "image_engine", "video_engine",
               "use_end_frame", "continue_from_prev", "origin")


def _detail(s, sv, shot: Shot, progress=None) -> ShotDetail:
    ep = s.get(Episode, shot.episode_id)
    return shot_detail(s, sv, shot, ep.project, ep.project.style_bible, progress=progress)


def _order_ids(s, episode_id: int) -> list[int]:
    return [x.id for x in renumber_shots(s, episode_id)]


@router.get("/shots/{shot_id}", response_model=ShotDetail)
def get_shot(shot_id: int, request: Request):
    sv = services(request)
    with sv.db.session() as s:
        return _detail(s, sv, get_or_404(s, Shot, shot_id, "镜头"), manager(request).progress)


@router.post("/episodes/{episode_id}/shots", status_code=201, response_model=ShotDetail)
def create_shot(episode_id: int, body: ShotIn, request: Request):
    sv = services(request)
    fields = body.model_dump(exclude_unset=True, exclude={"after_shot_id"})
    check_engines(sv, fields.get("image_engine"), fields.get("video_engine"))
    with sv.db.session() as s:
        get_or_404(s, Episode, episode_id, "剧集")
        ids = _order_ids(s, episode_id)
        if body.after_shot_id is not None and body.after_shot_id not in ids:
            raise LookupError(f"这一集没有镜头 {body.after_shot_id}")
        shot = Shot(episode_id=episode_id, order=len(ids), origin="manual", **fields)
        s.add(shot)
        s.flush()
        pos = ids.index(body.after_shot_id) + 1 if body.after_shot_id is not None else len(ids)
        ids.insert(pos, shot.id)
        renumber_shots(s, episode_id, ids)
        return _detail(s, sv, shot)


@router.patch("/shots/{shot_id}", response_model=ShotDetail)
def patch_shot(shot_id: int, body: ShotFields, request: Request):
    sv = services(request)
    data = body.model_dump(exclude_unset=True)
    check_engines(sv, data.get("image_engine"), data.get("video_engine"))
    with sv.db.session() as s:
        shot = get_or_404(s, Shot, shot_id, "镜头")
        for k, v in data.items():
            if v is None and k not in ("image_engine", "video_engine"):
                continue  # 只有引擎覆盖可以清空（回到项目默认）
            setattr(shot, k, v)
        s.flush()
        return _detail(s, sv, shot)


@router.delete("/shots/{shot_id}", status_code=204)
def delete_shot(shot_id: int, request: Request):
    with services(request).db.session() as s:
        shot = get_or_404(s, Shot, shot_id, "镜头")
        ep_id = shot.episode_id
        delete_slots(s, "shot", [shot.id])
        s.delete(shot)
        s.flush()
        renumber_shots(s, ep_id)
    return Response(status_code=204)


@router.post("/episodes/{episode_id}/shots/reorder", response_model=list[ShotOut])
def reorder_shots(episode_id: int, body: ReorderIn, request: Request):
    with services(request).db.session() as s:
        get_or_404(s, Episode, episode_id, "剧集")
        return [ShotOut.model_validate(x) for x in renumber_shots(s, episode_id, body.shot_ids)]


@router.post("/shots/{shot_id}/split", response_model=list[ShotOut])
def split_shot(shot_id: int, request: Request):
    with services(request).db.session() as s:
        shot = get_or_404(s, Shot, shot_id, "镜头")
        half = round(float(shot.duration) / 2, 2)
        rest = round(float(shot.duration) - half, 2)
        first = [d for d in shot.dialogue or [] if float(d.get("start", 0)) < half]
        second = [{**d, "start": round(float(d["start"]) - half, 2),
                   "end": round(float(d["end"]) - half, 2)}
                  for d in shot.dialogue or [] if float(d.get("start", 0)) >= half]
        new = Shot(episode_id=shot.episode_id, order=shot.order,
                   **{k: getattr(shot, k) for k in COPY_FIELDS},
                   duration=rest, dialogue=second, transition_out=shot.transition_out)
        shot.duration, shot.dialogue, shot.transition_out = half, first, "hard_cut"
        s.add(new)
        s.flush()
        ep = s.get(Episode, shot.episode_id)
        ids = [x.id for x in ep.shots if x.id != new.id]
        ids.insert(ids.index(shot.id) + 1, new.id)
        renumber_shots(s, shot.episode_id, ids)
        return [ShotOut.model_validate(shot), ShotOut.model_validate(new)]
