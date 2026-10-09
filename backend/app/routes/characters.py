"""角色卡：本地 AI 抽定妆照、切换造型、AI 推荐造型、按剧本推荐角色、三视图。"""
from __future__ import annotations

from fastapi import APIRouter, Request

from backend.app.deps import get_or_404, manager, services
from backend.app.routes.projects import character_out
from backend.app.schemas import (CharacterOut, PortraitDrawIn, RecommendIn, RunOut, SuggestIn,
                                 TurnaroundGenerateIn, TurnaroundPlanPatch)
from backend.domain.characters import portrait_of, use_portrait
from backend.domain.turnaround import VIEW_KEYS, VIEW_LABELS
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


@router.post("/characters/{char_id}/turnaround/plan", response_model=RunOut)
def turnaround_plan(char_id: int, request: Request):
    """三视图第一步：LLM 按外貌提示词写 9 格视角描述，写好后用户可以改，再逐格出图。"""
    sv = services(request)
    with sv.db.session() as s:
        c = get_or_404(s, Character, char_id, "角色")
        if not c.visual_prompt.strip():
            raise JobError(f"角色 {c.name} 还没有外貌提示词，先写一个或用 AI 推荐造型")
        project_id = c.project_id
    jid = manager(request).submit("character.turnaround_plan", {"character_id": char_id},
                                  project_id=project_id, owner_type="character", owner_id=char_id)
    return RunOut(job_ids=[jid])


@router.patch("/characters/{char_id}/turnaround/plan", response_model=CharacterOut)
def patch_turnaround_plan(char_id: int, body: TurnaroundPlanPatch, request: Request):
    """改某几格的视角描述（只改给出的格）。改了的格已出的图按哈希规则变过期。"""
    with services(request).db.session() as s:
        c = get_or_404(s, Character, char_id, "角色")
        c.turnaround_plan = {**(c.turnaround_plan or {}),
                             **{k: v.strip() for k, v in body.views.items()}}
        s.flush()
        return character_out(s, c)


@router.post("/characters/{char_id}/turnaround/generate", response_model=RunOut)
def turnaround_generate(char_id: int, body: TurnaroundGenerateIn, request: Request):
    """逐格出三视图：每格一个 GPU 任务，不给 views 就 9 格全出。"""
    sv = services(request)
    views = list(dict.fromkeys(body.views)) if body.views else list(VIEW_KEYS)
    with sv.db.session() as s:
        c = get_or_404(s, Character, char_id, "角色")
        plan = c.turnaround_plan or {}
        missing = [VIEW_LABELS[v] for v in views if not (plan.get(v) or "").strip()]
        if missing:
            raise JobError(f"角色 {c.name} 的三视图还缺视角描述：{'、'.join(missing)}，先生成视角描述")
        if portrait_of(s, char_id) is None:
            raise JobError(f"角色 {c.name} 还没有选定定妆照，三视图要用它锁脸")
        project_id, engine_name = c.project_id, c.project.image_engine
    try:
        family = sv.engines.image(engine_name).family
    except KeyError:
        family = None
    m = manager(request)
    return RunOut(job_ids=[m.submit("image.turnaround", {"character_id": char_id, "view": v},
                                    project_id=project_id, owner_type="character",
                                    owner_id=char_id, model_family=family)
                           for v in views])
