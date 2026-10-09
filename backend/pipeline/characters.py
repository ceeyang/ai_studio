"""角色卡：本地图像引擎抽定妆照（image.portrait），LLM 推荐造型（character.suggest）
和按剧本推荐缺的角色（character.recommend）。"""
from __future__ import annotations

import shutil

from backend.domain.characters import add_portrait
from backend.domain.hashing import input_hash
from backend.domain.models import Character, Episode
from backend.engines.base import ImageRequest
from backend.llm.schemas import CharacterRecommendations, DesignOptions
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
