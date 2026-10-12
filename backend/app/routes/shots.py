"""镜头：增删改、排序、拆分、并进下一镜做成多拍镜头。"""
from __future__ import annotations

from fastapi import APIRouter, Request, Response

from backend.app.deps import check_engines, get_or_404, manager, read_upload, services
from backend.app.schemas import ReorderIn, ShotDetail, ShotEngineAdviceOut, ShotFields, ShotIn, ShotOut
from backend.app.views import shot_detail
from backend.domain.archive import archive_and_delete_slots
from backend.domain.beats import BEATS_MAX, check_beats, generation_unit, scale_beats
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


@router.get("/shots/{shot_id}/engine-advice", response_model=ShotEngineAdviceOut)
def get_shot_engine_advice(shot_id: int, request: Request):
    """按镜头推荐视频引擎：难度、原因、满足硬性要求的引擎排好序（简单镜头先便宜的，难镜头先强的）。只是建议。"""
    from backend.app.routes.system import video_engine_infos
    from backend.domain.engine_advice import advise_shot

    sv = services(request)
    infos = video_engine_infos(sv)
    with sv.db.session() as s:
        shot = get_or_404(s, Shot, shot_id, "镜头")
        ep = s.get(Episode, shot.episode_id)
        adv = advise_shot(shot, infos, shot.video_engine or ep.project.video_engine)
        return ShotEngineAdviceOut.model_validate(adv)


@router.post("/episodes/{episode_id}/shots", status_code=201, response_model=ShotDetail)
def create_shot(episode_id: int, body: ShotIn, request: Request):
    sv = services(request)
    fields = body.model_dump(exclude_unset=True, exclude={"after_shot_id"})
    check_engines(sv, fields.get("image_engine"), fields.get("video_engine"))
    if "beats" in fields:
        fields["beats"] = check_beats(fields["beats"], fields.get("duration") or 5.0)
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


def _patch_beats(shot: Shot, data: dict) -> None:
    """PATCH 里的多拍：给了 beats 就按（新）时长校验；只改了时长的多拍镜头，拍按比例跟着缩放。
    不合规时抛 ValueError（400，中文说明）。"""
    duration = float(data.get("duration") or shot.duration)
    if "beats" in data:
        data["beats"] = check_beats(data["beats"], duration)
    elif shot.beats and data.get("duration") is not None and abs(duration - float(shot.duration)) > 1e-6:
        try:
            data["beats"] = check_beats(scale_beats(shot.beats, duration), duration)
        except ValueError as e:
            raise ValueError(f"改成 {duration:g} 秒后多拍镜头放不下：{e}；先在下面改拍，或少几拍") from None


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
        _patch_beats(shot, data)
        for k, v in data.items():
            if v is None and k not in ("image_engine", "video_engine", "video_mode", "beats"):
                continue  # 只有引擎覆盖、生成方式、多拍可以清空（回到项目默认 / 首帧模式 / 普通镜头）
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
        beats_a = beats_b = None
        if shot.beats and len(shot.beats) >= 2:
            # 多拍镜头：在离中间最近的拍边界上拆，两半各拿自己的拍（剩一拍就是普通镜头）
            k = min(range(1, len(shot.beats)),
                    key=lambda j: abs(float(shot.beats[j - 1]["end"]) - half))
            half = round(float(shot.beats[k - 1]["end"]), 2)
            beats_a = shot.beats[:k] if k >= 2 else None
            tail = [{**b, "start": round(float(b["start"]) - half, 2), "end": round(float(b["end"]) - half, 2)}
                    for b in shot.beats[k:]]
            beats_b = tail if len(tail) >= 2 else None
        rest = round(float(shot.duration) - half, 2)
        first = [d for d in shot.dialogue or [] if float(d.get("start", 0)) < half]
        second = [{**d, "start": round(float(d["start"]) - half, 2),
                   "end": round(float(d["end"]) - half, 2)}
                  for d in shot.dialogue or [] if float(d.get("start", 0)) >= half]
        new = Shot(episode_id=shot.episode_id, order=shot.order,
                   **{k: getattr(shot, k) for k in COPY_FIELDS},
                   duration=rest, dialogue=second, transition_out=shot.transition_out, beats=beats_b)
        shot.duration, shot.dialogue, shot.transition_out = half, first, "hard_cut"
        shot.beats = beats_a
        s.add(new)
        s.flush()
        ep = s.get(Episode, shot.episode_id)
        ids = [x.id for x in ep.shots if x.id != new.id]
        ids.insert(ids.index(shot.id) + 1, new.id)
        renumber_shots(s, shot.episode_id, ids)
        return [ShotOut.model_validate(shot), ShotOut.model_validate(new)]


def _as_beats(sh: Shot, offset: float) -> list[dict]:
    """一个镜头变成多拍镜头里的几拍：本来就是多拍的按拍平移，否则整镜算一拍。拍的 action 发给视频引擎
    （时间戳提示词全英文），所以用英文运动提示词，没有才用中文动作。"""
    if sh.beats:
        return [{**b, "start": round(float(b["start"]) + offset, 2),
                 "end": round(float(b["end"]) + offset, 2)} for b in sh.beats]
    return [{"start": round(offset, 2), "end": round(offset + float(sh.duration), 2),
             "shot_size": sh.shot_size or "", "camera_move": sh.camera_move or "",
             "action": (sh.motion_prompt or "").strip() or (sh.action or "").strip() or "continue"}]


def _union(a: list, b: list, key) -> list:
    out, seen = [], set()
    for x in [*(a or []), *(b or [])]:
        k = key(x)
        if k not in seen:
            seen.add(k)
            out.append(x)
    return out


def _join_prompt(a: str, b: str) -> str:
    """两段英文提示词接起来；前一段结尾的「No background music.」只留一句（在最后）。"""
    a, b = (a or "").strip(), (b or "").strip()
    if a and b and a.endswith("No background music.") and b.endswith("No background music."):
        a = a[: -len("No background music.")].rstrip()
    return " ".join(x for x in (a, b) if x)


@router.post("/shots/{shot_id}/merge-next-as-beats", response_model=ShotDetail)
def merge_next_as_beats(shot_id: int, request: Request):
    """固定时长视频引擎（设计文档 2026-10-12 固定时长引擎 §3.6）：把这一镜和同一段落里的下一镜合成一个多拍镜头，
    一次生成用满。时长相加、两镜各成一拍（本来就是多拍的按拍接上）、下一镜的台词时间往后挪、出场角色和道具
    合并、首帧提示词用这一镜的；下一镜删掉（候选进「历史候选」）。合起来超过一次生成的时长、下一镜在别的段落、
    拍数超过 4 拍时拒绝。"""
    sv = services(request)
    with sv.db.session() as s:
        shot = get_or_404(s, Shot, shot_id, "镜头")
        ep = s.get(Episode, shot.episode_id)
        ordered = renumber_shots(s, shot.episode_id)
        i = next(k for k, x in enumerate(ordered) if x.id == shot.id)
        if i + 1 >= len(ordered):
            raise JobError(f"{shot.code} 是这一集最后一镜，没有下一镜可以并进来")
        nxt = ordered[i + 1]
        if nxt.scene_block_id != shot.scene_block_id:
            raise JobError(f"{nxt.code} 在另一个段落（换了场景），不能和 {shot.code} 合成一个多拍镜头")
        engine_name = shot.video_engine or ep.project.video_engine
        try:
            caps = sv.engines.video(engine_name).caps
        except KeyError as e:
            raise JobError(str(e)) from None
        unit = generation_unit(caps)
        if unit is None:
            raise JobError(f"{engine_name} 能按镜头时长出视频，不用合并成多拍镜头")
        total = round(float(shot.duration) + float(nxt.duration), 2)
        if total > unit + 0.2:
            raise JobError(f"{shot.code}（{float(shot.duration):g} 秒）和 {nxt.code}（{float(nxt.duration):g} 秒）"
                           f"加起来 {total:g} 秒，超过一次生成的 {unit:g} 秒，不能合成一个多拍镜头")
        beats = _as_beats(shot, 0.0) + _as_beats(nxt, float(shot.duration))
        if len(beats) > BEATS_MAX:
            raise JobError(f"合起来有 {len(beats)} 拍，多拍镜头最多 {BEATS_MAX} 拍")
        beats = check_beats(beats, total)
        off = float(shot.duration)
        moved = [{**d, "start": round(float(d.get("start", 0)) + off, 2),
                  "end": round(float(d.get("end", 0)) + off, 2)} for d in nxt.dialogue or []]
        shot.beats = beats
        shot.duration = total
        shot.dialogue = [*(shot.dialogue or []), *moved]
        shot.cast = _union(shot.cast, nxt.cast, lambda c: c.get("character_id") if isinstance(c, dict) else c)
        shot.props = _union(shot.props, nxt.props, lambda p: p)
        shot.action = "；".join(x for x in ((shot.action or "").strip(), (nxt.action or "").strip()) if x)
        shot.motion_prompt = _join_prompt(shot.motion_prompt, nxt.motion_prompt)
        shot.audio_prompt = _join_prompt(shot.audio_prompt, nxt.audio_prompt)
        shot.transition_out = nxt.transition_out
        # 下一镜和删镜头一样：候选进「历史候选」，槽位删掉
        archive_and_delete_slots(s, [nxt.id], "merge_shot")
        s.delete(nxt)
        s.flush()
        renumber_shots(s, shot.episode_id)
        return _detail(s, sv, shot)


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
