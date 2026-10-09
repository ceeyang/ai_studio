"""角色卡：本地图像引擎抽定妆照（image.portrait），LLM 推荐造型（character.suggest）、
按剧本推荐缺的角色（character.recommend），以及三视图（character.turnaround_plan 写 9 格视角描述，
image.turnaround 逐格出图）。"""
from __future__ import annotations

import shutil

from backend.domain.characters import add_portrait, portrait_of
from backend.domain.hashing import input_hash
from backend.domain.models import Character, Episode
from backend.engines.base import ImageRequest
from backend.domain.slots import add_candidate, get_or_create_slot
from backend.domain.turnaround import (VIEW_FRAMES, VIEW_KEYS, VIEW_LABELS, slot_kind,
                                       turnaround_hash, turnaround_inputs)
from backend.llm.schemas import CharacterRecommendations, DesignOptions, TurnaroundPlan
from backend.pipeline.common import load_episode
from backend.pipeline.keyframes import run_comfy
from backend.pipeline.prompting import compose_image_prompt, join_negative, shot_seed
from backend.queue.errors import JobError
from backend.queue.handlers import HANDLERS

PORTRAIT_ASPECT = "9:16"
PORTRAIT_FRAME = ("Character reference portrait of one single person: front-facing half-body shot "
                  "from the waist up, standing upright and looking straight into the camera, "
                  "relaxed neutral expression, arms down at the sides, centered in frame.")
PORTRAIT_BACKDROP = ("Plain seamless neutral light-grey studio backdrop, soft even studio lighting, "
                     "no props, no other people, no text, no watermark.")
LLM_TASK = "character"


def portrait_prompt(visual_prompt: str, anchor: str) -> str:
    return compose_image_prompt(f"{PORTRAIT_FRAME} {visual_prompt.strip()} {PORTRAIT_BACKDROP}",
                                anchor)


def _load_char(s, char_id: int) -> Character:
    ch = s.get(Character, char_id)
    if ch is None:
        raise JobError(f"找不到角色 {char_id}")
    return ch


def _episode_ctx(ep: Episode) -> dict:
    return {"number": ep.number, "title": ep.title, "source_text": (ep.source_text or "")[:6000],
            "blocks": [b.text for b in ep.scene_blocks]}


@HANDLERS.register("image.portrait", gpu=True)
def run_portrait(ctx, payload: dict) -> dict:
    """出一张正面半身定妆照候选。visual_prompt 不给就用角色当前的外貌提示词。"""
    sv = ctx.services
    char_id = int(payload["character_id"])
    with sv.db.session() as s:
        ch = _load_char(s, char_id)
        proj, style = ch.project, ch.project.style_bible
        vp = (payload.get("visual_prompt") or ch.visual_prompt or "").strip()
        if not vp:
            raise JobError(f"角色 {ch.name} 还没有外貌提示词，先写一个或用 AI 推荐造型")
        engine_name = proj.image_engine
        try:
            engine = sv.engines.image(engine_name)
        except KeyError as e:
            raise JobError(str(e)) from None
        prompt = portrait_prompt(vp, style.anchor_text if style else "")
        negative = join_negative(proj.negative, style.negative if style else "")
        seed = shot_seed(proj.seed, char_id, ctx.job_id)
        slug, name = proj.slug, ch.name
    call = engine.build(ImageRequest(prompt=prompt, negative=negative, aspect=PORTRAIT_ASPECT,
                                     seed=seed, prefix=f"studio/{slug}/portrait_{char_id}"))
    ctx.log(f"定妆照 {name} · {engine_name} · seed {seed}")
    files, run_dir = run_comfy(ctx, call, slug)
    dst = sv.media.new_media_path(slug, "portrait", files[0].suffix)
    shutil.copyfile(files[0], dst)
    with sv.db.session() as s:
        c = add_portrait(s, char_id, file_path=sv.media.rel(dst), engine=engine_name,
                         params={**call.meta, "visual_prompt": vp,
                                 "workflow": sv.media.rel(run_dir / "workflow.api.json")},
                         input_hash=input_hash("portrait", prompt, negative, engine_name, seed),
                         job_id=ctx.job_id)
        cid = c.id
    ctx.log(f"定妆照 {name}：候选 {cid}")
    return {"candidate_ids": [cid], "file": sv.media.rel(dst)}


@HANDLERS.register("character.suggest")
def run_suggest(ctx, payload: dict) -> dict:
    """读剧本和人物描述，给角色出 count 套造型方案，存进 character.design_options。"""
    sv = ctx.services
    char_id = int(payload["character_id"])
    count = int(payload.get("count") or 3)
    with sv.db.session() as s:
        ch = _load_char(s, char_id)
        proj, style = ch.project, ch.project.style_bible
        if payload.get("episode_id"):
            ep, _ = load_episode(s, int(payload["episode_id"]))
            eps = [ep]
        else:
            eps = list(proj.episodes)
        system, user = sv.prompts(proj.slug).render(
            "character_suggest", count=count, project_title=proj.title, char_name=ch.name,
            description=ch.description, current_prompt=ch.visual_prompt,
            core_features=ch.core_features,
            others=[c.name for c in proj.characters if c.id != ch.id],
            anchor=style.anchor_text if style else "",
            character_rules=style.character_rules if style else "",
            episodes=[_episode_ctx(e) for e in eps])
        name = ch.name

    def check(out: DesignOptions) -> None:
        if len(out.options) != count:
            raise ValueError(f"要正好 {count} 个方案，现在是 {len(out.options)} 个")

    out = sv.llm.generate(LLM_TASK, system, user, DesignOptions, check=check, log=ctx.log,
                          manual=payload.get("manual") or {}, key=f"character.suggest:{char_id}")
    opts = [o.model_dump() for o in out.options]
    with sv.db.session() as s:
        _load_char(s, char_id).design_options = opts
    ctx.log(f"{name} 的造型方案：{'、'.join(o['label'] for o in opts)}")
    return {"options": len(opts)}


@HANDLERS.register("character.recommend")
def run_recommend(ctx, payload: dict) -> dict:
    """按剧本推荐还没建的角色。只推荐，结果放在任务 result 里，由用户一键添加。"""
    sv = ctx.services
    episode_id = int(payload["episode_id"])
    count = int(payload.get("count") or 3)
    with sv.db.session() as s:
        ep, proj = load_episode(s, episode_id)
        if not (ep.source_text or "").strip() and not ep.scene_blocks:
            raise JobError("这一集还没有原文或段落，没法按剧本推荐角色")
        style = proj.style_bible
        existing = [c.name for c in proj.characters]
        system, user = sv.prompts(proj.slug).render(
            "character_recommend", count=count, project_title=proj.title, existing=existing,
            anchor=style.anchor_text if style else "",
            character_rules=style.character_rules if style else "",
            episode=_episode_ctx(ep))
    out = sv.llm.generate(LLM_TASK, system, user, CharacterRecommendations, log=ctx.log,
                          manual=payload.get("manual") or {},
                          key=f"character.recommend:{episode_id}")
    have = set(existing)
    chars = [c.model_dump() for c in out.characters if c.name.strip() not in have][:count]
    ctx.log(f"推荐角色：{'、'.join(c['name'] for c in chars) or '（没有需要补的）'}")
    return {"characters": chars}


@HANDLERS.register("character.turnaround_plan")
def run_turnaround_plan(ctx, payload: dict) -> dict:
    """LLM 按外貌提示词给三视图 9 格写英文视角描述，存进 character.turnaround_plan（整份替换）。"""
    sv = ctx.services
    char_id = int(payload["character_id"])
    with sv.db.session() as s:
        ch = _load_char(s, char_id)
        if not (ch.visual_prompt or "").strip():
            raise JobError(f"角色 {ch.name} 还没有外貌提示词，先写一个或用 AI 推荐造型")
        proj, style = ch.project, ch.project.style_bible
        system, user = sv.prompts(proj.slug).render(
            "character_turnaround", project_title=proj.title, char_name=ch.name,
            description=ch.description, visual_prompt=ch.visual_prompt,
            core_features=ch.core_features,
            anchor=style.anchor_text if style else "",
            character_rules=style.character_rules if style else "",
            views=[{"key": k, "label": VIEW_LABELS[k], "frame": VIEW_FRAMES[k]} for k in VIEW_KEYS])
        name = ch.name
    out = sv.llm.generate(LLM_TASK, system, user, TurnaroundPlan, log=ctx.log,
                          manual=payload.get("manual") or {},
                          key=f"character.turnaround_plan:{char_id}")
    by_view = {c.view: c.description for c in out.views}
    plan = {k: by_view[k] for k in VIEW_KEYS}
    with sv.db.session() as s:
        _load_char(s, char_id).turnaround_plan = plan
    ctx.log(f"{name} 的三视图描述已写好（9 格）")
    return {"views": len(plan)}


@HANDLERS.register("image.turnaround", gpu=True)
def run_turnaround(ctx, payload: dict) -> dict:
    """出三视图的一格：项目图像引擎 + 这一格的视角描述，引擎支持参考图时带选定的定妆照锁脸。"""
    sv = ctx.services
    char_id = int(payload["character_id"])
    view = str(payload.get("view") or "")
    if view not in VIEW_KEYS:
        raise JobError(f"没有这个三视图视角：{view}，可选：{', '.join(VIEW_KEYS)}")
    with sv.db.session() as s:
        ch = _load_char(s, char_id)
        portrait = portrait_of(s, char_id)
        if portrait is None:
            raise JobError(f"角色 {ch.name} 还没有选定定妆照，三视图要用它锁脸")
        inputs = turnaround_inputs(ch, view)
        if inputs is None:
            raise JobError(f"角色 {ch.name} 的三视图「{VIEW_LABELS[view]}」还没有视角描述，先生成视角描述")
        prompt, negative, engine_name = inputs
        try:
            engine = sv.engines.image(engine_name)
        except KeyError as e:
            raise JobError(str(e)) from None
        refs = [sv.media.abs(portrait.file_path)] if engine.caps.max_refs > 0 else []
        seed = shot_seed(ch.project.seed, char_id, ctx.job_id)
        slug, name, portrait_id = ch.project.slug, ch.name, portrait.id
    call = engine.build(ImageRequest(prompt=prompt, negative=negative, aspect=PORTRAIT_ASPECT,
                                     seed=seed, refs=refs,
                                     prefix=f"studio/{slug}/turnaround_{char_id}_{view}"))
    ctx.log(f"三视图 {name} · {VIEW_LABELS[view]} · {engine_name} · seed {seed}"
            f"{' · 定妆照锁脸' if refs else ''}")
    files, run_dir = run_comfy(ctx, call, slug)
    dst = sv.media.new_media_path(slug, "turnaround", files[0].suffix)
    shutil.copyfile(files[0], dst)
    with sv.db.session() as s:
        slot = get_or_create_slot(s, "character", char_id, slot_kind(view))
        # 自动选定规则同首帧：还没有选定或选定的已过期才换成这张
        c = add_candidate(s, slot, file_path=sv.media.rel(dst), engine=engine_name,
                          params={**call.meta, "view": view, "portrait_candidate_id": portrait_id,
                                  "workflow": sv.media.rel(run_dir / "workflow.api.json")},
                          input_hash=turnaround_hash(prompt, negative, engine_name, portrait_id),
                          job_id=ctx.job_id)
        cid = c.id
    ctx.log(f"三视图 {name} · {VIEW_LABELS[view]}：候选 {cid}")
    return {"candidate_ids": [cid], "file": sv.media.rel(dst)}
