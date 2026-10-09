"""角色卡：本地 AI 抽定妆照、切换造型、AI 推荐造型、按剧本推荐角色。"""
from __future__ import annotations

from fastapi import APIRouter, Request

from backend.app.deps import get_or_404, manager, services
from backend.app.routes.projects import character_out
from backend.app.schemas import CharacterOut, PortraitDrawIn, RecommendIn, RunOut, SuggestIn
from backend.domain.characters import use_portrait
from backend.domain.models import Character, Episode
from backend.queue.errors import JobError

router = APIRouter(prefix="/api", tags=["characters"])


@router.post("/characters/{char_id}/portraits/draw", response_model=RunOut)
def draw_portraits(char_id: int, body: PortraitDrawIn, request: Request):
    """抽卡：用项目的本地图像引擎出 count 张正面半身定妆照，每张一个 GPU 任务。"""
    sv = services(request)
    vp = (body.visual_prompt or "").strip()
    with sv.db.session() as s:
        c = get_or_404(s, Character, char_id, "角色")
        if not vp and not c.visual_prompt.strip():
            raise JobError(f"角色 {c.name} 还没有外貌提示词，先写一个或用 AI 推荐造型")
        project_id, engine_name = c.project_id, c.project.image_engine
    try:
        family = sv.engines.image(engine_name).family
    except KeyError:
        family = None
    payload: dict = {"character_id": char_id}
    if vp:
        payload["visual_prompt"] = vp
    m = manager(request)
    return RunOut(job_ids=[m.submit("image.portrait", payload, project_id=project_id,
                                    owner_type="character", owner_id=char_id,
                                    model_family=family)
                           for _ in range(body.count)])


@router.post("/characters/{char_id}/portraits/{candidate_id}/use", response_model=CharacterOut)
def use(char_id: int, candidate_id: int, request: Request):
    """切换角色：选定这张定妆照，它带着提示词时角色外貌提示词一起换。出场镜头按哈希规则变过期。"""
    with services(request).db.session() as s:
        c = get_or_404(s, Character, char_id, "角色")
        use_portrait(s, c, candidate_id)
        return character_out(s, c)


@router.post("/characters/{char_id}/suggest", response_model=RunOut)
def suggest(char_id: int, body: SuggestIn, request: Request):
    """AI 推荐造型：LLM 读剧本给 count 套外观方案，结果写进角色的 design_options。"""
    sv = services(request)
    with sv.db.session() as s:
        c = get_or_404(s, Character, char_id, "角色")
        project_id = c.project_id
        if body.episode_id is not None:
            get_or_404(s, Episode, body.episode_id, "剧集")
    payload: dict = {"character_id": char_id, "count": body.count}
    if body.episode_id is not None:
        payload["episode_id"] = body.episode_id
    jid = manager(request).submit("character.suggest", payload, project_id=project_id,
                                  owner_type="character", owner_id=char_id)
    return RunOut(job_ids=[jid])


@router.post("/episodes/{episode_id}/characters/recommend", response_model=RunOut)
def recommend(episode_id: int, body: RecommendIn, request: Request):
    """按剧本推荐角色表里还没有的人物，结果在任务 result.characters 里。"""
    sv = services(request)
    with sv.db.session() as s:
        project_id = get_or_404(s, Episode, episode_id, "剧集").project_id
    jid = manager(request).submit("character.recommend", {"episode_id": episode_id, "count": body.count},
                                  project_id=project_id, owner_type="episode", owner_id=episode_id)
    return RunOut(job_ids=[jid])
