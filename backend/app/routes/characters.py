"""角色卡：本地 AI 抽定妆照、切换造型、AI 推荐造型、按剧本推荐角色、三视图。"""
from __future__ import annotations

from fastapi import APIRouter, Request

from backend.app.deps import check_engines, get_or_404, manager, services
from backend.app.routes.projects import character_out, read_image_upload
from backend.app.schemas import (CharacterOut, PortraitDrawIn, RecommendIn, RefreshLooksIn,
                                 RefreshLooksOut, RunOut, SuggestIn,
                                 TurnaroundCropIn, TurnaroundGenerateIn, TurnaroundLookPatch,
                                 TurnaroundSelectIn)
from backend.domain.characters import (appearance_ref_of, known_looks, portrait_of,
                                       refresh_shot_looks, set_appearance_ref, set_photo_look,
                                       use_portrait)
from backend.domain.turnaround import (SHEETS, crop_cells, look_of, select_sheet,
                                       sheet_slot_kind)
from backend.domain.models import Candidate, Character, Episode, Slot
from backend.domain.settings import portrait_engine
from backend.engines.registry import check_ready
from backend.pipeline.characters import sheet_engine_name
from backend.pipeline.storyboard import rebase_after_look_change
from backend.queue.errors import JobError

router = APIRouter(prefix="/api", tags=["characters"])


@router.post("/characters/{char_id}/portraits/draw", response_model=RunOut)
def draw_portraits(char_id: int, body: PortraitDrawIn, request: Request):
    """抽卡：出 count 张正面半身定妆照，每张一个任务。引擎在这里一次定下（手选 → 设置页定妆照这一步 →
    项目的图像引擎）写进 payload，队列（model_family）和执行用的是同一个。"""
    sv = services(request)
    vp = (body.visual_prompt or "").strip()
    with sv.db.session() as s:
        c = get_or_404(s, Character, char_id, "角色")
        if not vp and not c.visual_prompt.strip():
            raise JobError(f"角色 {c.name} 还没有外貌提示词，先写一个或用 AI 推荐造型")
        project_id = c.project_id
        engine_name = portrait_engine(sv, c.project.image_engine, body.engine,
                                     preset=c.project.style_preset or "")
    check_engines(sv, engine_name, None)
    eng = sv.engines.image(engine_name)
    check_ready(eng)
    payload: dict = {"character_id": char_id, "engine": engine_name}
    if vp:
        payload["visual_prompt"] = vp
    m = manager(request)
    return RunOut(job_ids=[m.submit("image.portrait", payload, project_id=project_id,
                                    owner_type="character", owner_id=char_id,
                                    model_family=eng.family)
                           for _ in range(body.count)])


@router.post("/characters/{char_id}/portraits/{candidate_id}/use", response_model=CharacterOut)
def use(char_id: int, candidate_id: int, request: Request):
    """切换角色：选定这张定妆照，它带着提示词时角色外貌提示词一起换。出场镜头按哈希规则变过期。"""
    with services(request).db.session() as s:
        c = get_or_404(s, Character, char_id, "角色")
        use_portrait(s, c, candidate_id)
        return character_out(s, c, sv=services(request))


@router.post("/characters/{char_id}/shots/refresh-look", response_model=RefreshLooksOut)
def refresh_look(char_id: int, body: RefreshLooksIn, request: Request):
    """把镜头里的外貌更新为当前造型：镜头 image_prompt 里一字不差的旧外貌换成外貌占位符
    （出图时按景别填当前外貌），首帧随之过期。"""
    sv = services(request)
    with sv.db.session() as s:
        c = get_or_404(s, Character, char_id, "角色")
        changed: dict[int, str] = {}
        old_looks = known_looks(s, c, body.old)
        res = refresh_shot_looks(s, c, body.old, changed)
        # v2 分镜的段落记录跟着换成现在的外貌，下一次整集运行不会要求删光这些段重出
        res["blocks_rebased"] = rebase_after_look_change(s, sv, c, old_looks, changed) if changed else 0
        return RefreshLooksOut(**res)


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


@router.post("/characters/{char_id}/appearance-ref", response_model=CharacterOut)
async def upload_appearance_ref(char_id: int, request: Request):
    """「从参考图写外观」的参考照片：multipart 上传 file，或 JSON {"path"}。只给 LLM 看，不当出图参考图。"""
    data, name = await read_image_upload(request)
    sv = services(request)
    with sv.db.session() as s:
        c = get_or_404(s, Character, char_id, "角色")
        set_appearance_ref(s, sv.media, c, data, name)
        return character_out(s, c, sv=sv)


@router.post("/characters/{char_id}/appearance-from-photo", response_model=RunOut)
def appearance_from_photo(char_id: int, request: Request):
    """LLM 看参考照片写英文外观提示词（文本模型要能看图）。结果记在参考照片上，用户确认后再填进外观提示词。"""
    sv = services(request)
    with sv.db.session() as s:
        c = get_or_404(s, Character, char_id, "角色")
        if appearance_ref_of(s, char_id) is None:
            raise JobError(f"角色 {c.name} 还没有上传参考照片，先上传一张")
        project_id = c.project_id
    jid = manager(request).submit("character.from_photo", {"character_id": char_id},
                                  project_id=project_id, owner_type="character", owner_id=char_id)
    return RunOut(job_ids=[jid])


@router.delete("/characters/{char_id}/appearance-from-photo", response_model=CharacterOut)
def discard_photo_look(char_id: int, request: Request):
    """不用 LLM 按参考照片写的外观了（照片留着，可以再写一次）。"""
    sv = services(request)
    with sv.db.session() as s:
        c = get_or_404(s, Character, char_id, "角色")
        ref = appearance_ref_of(s, char_id)
        if ref is not None:
            set_photo_look(s, ref, None)
        return character_out(s, c, sv=sv)


@router.post("/episodes/{episode_id}/characters/recommend", response_model=RunOut)
def recommend(episode_id: int, body: RecommendIn, request: Request):
    """按剧本推荐角色表里还没有的人物，结果在任务 result.characters 里。"""
    sv = services(request)
    with sv.db.session() as s:
        project_id = get_or_404(s, Episode, episode_id, "剧集").project_id
    jid = manager(request).submit("character.recommend", {"episode_id": episode_id, "count": body.count},
                                  project_id=project_id, owner_type="episode", owner_id=episode_id)
    return RunOut(job_ids=[jid])


@router.post("/characters/{char_id}/turnaround/look", response_model=RunOut)
def turnaround_look(char_id: int, request: Request):
    """三视图可选的第一步：LLM 把外貌提示词整理成从头到脚的全身描述（补上下装和鞋），用户可改。"""
    sv = services(request)
    with sv.db.session() as s:
        c = get_or_404(s, Character, char_id, "角色")
        if not c.visual_prompt.strip():
            raise JobError(f"角色 {c.name} 还没有外貌提示词，先写一个或用 AI 推荐造型")
        project_id = c.project_id
    jid = manager(request).submit("character.turnaround_look", {"character_id": char_id},
                                  project_id=project_id, owner_type="character", owner_id=char_id)
    return RunOut(job_ids=[jid])


@router.patch("/characters/{char_id}/turnaround/look", response_model=CharacterOut)
def patch_turnaround_look(char_id: int, body: TurnaroundLookPatch, request: Request):
    """改全身描述。已出的设定图和切出来的格子按哈希规则变过期。"""
    with services(request).db.session() as s:
        c = get_or_404(s, Character, char_id, "角色")
        c.turnaround_look = body.look.strip()
        s.flush()
        return character_out(s, c, sv=services(request))


@router.post("/characters/{char_id}/turnaround/generate", response_model=RunOut)
def turnaround_generate(char_id: int, body: TurnaroundGenerateIn, request: Request):
    """出三视图设定图：每种（全身 / 头像）出 count 张候选，每张一个 GPU 任务。"""
    sv = services(request)
    sheets = list(dict.fromkeys(body.sheets)) if body.sheets else list(SHEETS)
    with sv.db.session() as s:
        c = get_or_404(s, Character, char_id, "角色")
        engine_name = body.engine or sheet_engine_name(sv, c.project)
        try:
            engine = sv.engines.image(engine_name)
        except KeyError as e:
            raise JobError(str(e)) from None
        check_ready(engine)
        if not look_of(c):
            raise JobError(f"角色 {c.name} 还没有外貌提示词，先写一个或用 AI 推荐造型")
        if engine.caps.max_refs > 0 and portrait_of(s, char_id) is None:
            raise JobError(f"角色 {c.name} 还没有选定定妆照，{engine_name} 出三视图要用它锁脸")
        project_id = c.project_id
    m = manager(request)
    return RunOut(job_ids=[m.submit("image.turnaround",
                                    {"character_id": char_id, "sheet": sh, "engine": engine_name},
                                    project_id=project_id, owner_type="character",
                                    owner_id=char_id, model_family=engine.family)
                           for sh in sheets for _ in range(body.count)])


def _sheet_or_404(sheet: str) -> str:
    if sheet not in SHEETS:
        raise LookupError(f"没有这种三视图设定图：{sheet}")
    return sheet


@router.post("/characters/{char_id}/turnaround/sheets/{sheet}/select", response_model=CharacterOut)
def select_turnaround_sheet(char_id: int, sheet: str, body: TurnaroundSelectIn, request: Request):
    """换选定的设定图并立即重新切图。用到这些格子的镜头按哈希规则变过期。"""
    sv = services(request)
    _sheet_or_404(sheet)
    with sv.db.session() as s:
        c = get_or_404(s, Character, char_id, "角色")
        cand = s.get(Candidate, body.candidate_id)
        slot = s.get(Slot, cand.slot_id) if cand else None
        if slot is None or slot.kind != sheet_slot_kind(sheet):
            raise LookupError(f"候选 {body.candidate_id} 不是{SHEETS[sheet].label}设定图")
        select_sheet(s, sv.media, c, body.candidate_id)
        return character_out(s, c, sv=services(request))


@router.post("/characters/{char_id}/turnaround/sheets/{sheet}/crop", response_model=CharacterOut)
def crop_turnaround_sheet(char_id: int, sheet: str, body: TurnaroundCropIn, request: Request):
    """重新切图：auto 按人物切（找不准时自动退回等宽），equal 直接等宽切。"""
    sv = services(request)
    _sheet_or_404(sheet)
    with sv.db.session() as s:
        c = get_or_404(s, Character, char_id, "角色")
        crop_cells(s, sv.media, c, sheet, body.method)
        return character_out(s, c, sv=services(request))
