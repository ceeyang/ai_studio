"""镜头：增删改、排序、拆分。"""
from __future__ import annotations

from fastapi import APIRouter, Request, Response

from backend.app.deps import check_engines, get_or_404, manager, read_upload, services
from backend.app.schemas import ReorderIn, ShotDetail, ShotFields, ShotIn, ShotOut
from backend.app.views import shot_detail
from backend.domain.archive import archive_and_delete_slots
from backend.domain.models import Episode, Shot
from backend.domain.refs import add_shot_ref, remove_shot_ref
from backend.domain.shots import renumber_shots
from backend.queue.errors import JobError

router = APIRouter(prefix="/api", tags=["shots"])

COPY_FIELDS = ("scene_block_id", "shot_size", "camera_move", "action", "cast", "props",
               "image_prompt", "motion_prompt", "audio_prompt", "image_engine", "video_engine",
               "use_end_frame", "continue_from_prev", "video_mode", "origin")


def check_placeholders(text: str, char_ids: set[int]) -> None:
    """手改 image_prompt：外貌占位符只能写成 {{char:编号}}、编号是本项目的角色；写坏了（少括号、
    写成名字）出图时会原样当成文字画进去，直接报错。"""
    from backend.pipeline.prompting import CHAR_TOKEN

    rest = CHAR_TOKEN.sub("", text or "")
    if "{{" in rest or "}}" in rest:
        raise JobError("图像提示词里有写坏的外貌占位符：只能写成 {{char:编号}}，如 {{char:3}}")
    bad = sorted({int(x) for x in CHAR_TOKEN.findall(text or "")} - char_ids)
    if bad:
        raise JobError(f"图像提示词里的外貌占位符 {'、'.join('{{char:%d}}' % x for x in bad)} "
                       "不是本项目的角色")


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
        if data.get("image_prompt"):
            ep = s.get(Episode, shot.episode_id)
            check_placeholders(data["image_prompt"], {c.id for c in ep.project.characters})
        for k, v in data.items():
            if v is None and k not in ("image_engine", "video_engine", "video_mode"):
                continue  # 只有引擎覆盖、生成方式可以清空（回到项目默认 / 首帧模式）
            setattr(shot, k, v)
        s.flush()
        return _detail(s, sv, shot)


@router.delete("/shots/{shot_id}", status_code=204)
def delete_shot(shot_id: int, request: Request):
    with services(request).db.session() as s:
        shot = get_or_404(s, Shot, shot_id, "镜头")
        ep_id = shot.episode_id
        archive_and_delete_slots(s, [shot.id], "delete_shot")   # 候选进「历史候选」
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


@router.post("/shots/{shot_id}/refs", response_model=ShotDetail)
async def upload_shot_ref(shot_id: int, request: Request):
    """上传一张构图 / 动作参考图（multipart 的 file 字段，每镜最多 2 张），首帧参考图里排在场景图后面。"""
    data, name = await read_upload(request)
    sv = services(request)
    with sv.db.session() as s:
        shot = get_or_404(s, Shot, shot_id, "镜头")
        slug = s.get(Episode, shot.episode_id).project.slug
        add_shot_ref(s, sv.media, shot, slug, data, name)
        return _detail(s, sv, shot)


@router.delete("/shots/{shot_id}/refs/{candidate_id}", response_model=ShotDetail)
def delete_shot_ref(shot_id: int, candidate_id: int, request: Request):
    """去掉一张构图参考图（文件留在磁盘上）。用过它的首帧按哈希规则变过期。"""
    sv = services(request)
    with sv.db.session() as s:
        shot = get_or_404(s, Shot, shot_id, "镜头")
        remove_shot_ref(s, shot_id, candidate_id)
        return _detail(s, sv, shot)
