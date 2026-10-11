"""角色卡：本地图像引擎抽定妆照（image.portrait），LLM 推荐造型（character.suggest）、
按剧本推荐缺的角色（character.recommend）、从参考照片写外观（character.from_photo），
以及三视图（character.turnaround_look 写全身描述，image.turnaround 出设定图并自动切成单个视角）。"""
from __future__ import annotations

import re
import shutil
from urllib.parse import quote

from backend.domain.characters import add_portrait, appearance_ref_of, portrait_of, set_photo_look
from backend.domain.hashing import input_hash
from backend.domain.settings import portrait_engine, sheet_engine
from backend.domain.models import Character, Episode
from backend.engines.base import ImageRequest
from backend.domain.slots import add_candidate, get_or_create_slot
from backend.domain.turnaround import (SHEETS, crop_cells, look_of,
                                       sheet_hash, sheet_inputs, sheet_slot_kind)
from backend.llm.schemas import CharacterRecommendations, DesignOptions, PhotoLook, TurnaroundLook
from backend.pipeline.common import load_episode
from backend.pipeline.keyframes import run_comfy
from backend.pipeline.prompting import (_UPPER_BODY, join_negative, photo_style, portrait_look,
                                        shot_seed)
from backend.queue.errors import JobError
from backend.queue.handlers import HANDLERS

# 定妆照优先出 3:4（比 9:16 矮，画面里放不下腿）；引擎没有 3:4 才退回 9:16
PORTRAIT_ASPECTS = ("3:4", "9:16")
# 默认的 zimage 是 cfg 1，负面提示词不起作用：构图全靠正面描述，只写要什么、不写「不要什么」
# （「legs out of frame」「no props」这类否定词在 cfg 1 下反而会把腿、道具招进画面）
PORTRAIT_FRAME = ("Character reference portrait of one single person: front-facing tight medium "
                  "shot cropped at the waist, the bottom edge of the frame cuts across the belt; "
                  "head, shoulders, chest and both arms fill the frame, standing upright and "
                  "looking straight into the camera, relaxed neutral expression, arms relaxed at "
                  "the sides, centered in frame.")
# 外貌后面再说一遍取景（krea2 / zimage 没有负面提示词，构图全靠正面描述）
PORTRAIT_REMINDER = ("Waist-up crop: the person stands upright facing the camera, the frame ends at "
                     "the waist.")
PORTRAIT_BACKDROP = ("Plain seamless neutral light-grey studio backdrop filling the entire "
                     "background, soft even studio lighting, the person alone against the empty "
                     "grey backdrop.")
PORTRAIT_NEGATIVE = ("full body, legs, feet, shoes, outdoor scene, room interior, street, rain, "
                     "scenery, props, multiple people")
LLM_TASK = "character"


def portrait_aspect(engine) -> str:
    sizes = getattr(getattr(engine, "caps", None), "sizes", None) or {}
    return next((a for a in PORTRAIT_ASPECTS if a in sizes), PORTRAIT_ASPECTS[-1])


def portrait_prompt(visual_prompt: str, anchor: str) -> str:
    """定妆照提示词：构图（正面、站着、腰部以上）→ 上半身外貌（去掉下半身、坐蹲姿势、手部细节）→
    再说一遍取景 → 中性影棚背景 → 风格锚里的成像词（去掉场景、天气、光源和雾、烟这类氛围词）。

    不整段拼风格锚：锚里的雨夜、灯泡、积水会和「纯色影棚背景」打架，出来的是挂在场景里的背景布，
    这张图再当参考图喂给视频模型时会被剪进镜头。"""
    style = photo_style(anchor)
    parts = [PORTRAIT_FRAME, portrait_upper_look(visual_prompt), PORTRAIT_REMINDER, PORTRAIT_BACKDROP]
    if style:
        # 写「Style」而不是「Photographic style」（和场景图一致）：动漫 / 国风预设不能被说成照片
        parts.append(f"Style: {style}.")
    return " ".join(parts)


_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def _lead(text: str) -> tuple[str, ...]:
    return tuple(re.findall(r"[a-z0-9'-]+", text.lower())[:3])


def portrait_upper_look(visual_prompt: str) -> str:
    """定妆照用的外貌：portrait_look（去掉下半身、坐蹲姿势、手部细节），再丢掉「主语分句被去掉」的句子。

    「Signature detail: the long cardigan hanging below her hips, a soft warm block of color against
    cool surroundings.」第一个分句写了 hips 被整段去掉，剩下「A soft warm block of color against cool
    surroundings.」——主语没了，还和灰背景打架。判断办法：结果里的一句，开头和原文任何一句的开头都
    对不上（说明它是从句子中间接上来的），而且自己也没写上衣 / 脸 / 发型这类上半身的东西，就丢掉。"""
    look = portrait_look(visual_prompt)
    starts = {_lead(s) for s in _SENTENCE.split((visual_prompt or "").strip()) if s}
    kept = [s for s in _SENTENCE.split(look)
            if s and (_lead(s) in starts or _UPPER_BODY.search(s))]
    return " ".join(kept) or look


def seed_label(call, seed: int) -> str:
    runner = getattr(call, "runner", "")
    if runner == "browser":
        return "无种子（网页出图每次不同）"
    return "无种子（API 出图每次不同）" if runner == "api" else f"seed {seed}"


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
    """出一张正面半身定妆照候选。visual_prompt 不给就用角色当前的外貌提示词；engine 不给用项目的图像引擎。"""
    sv = ctx.services
    char_id = int(payload["character_id"])
    with sv.db.session() as s:
        ch = _load_char(s, char_id)
        proj, style = ch.project, ch.project.style_bible
        vp = (payload.get("visual_prompt") or ch.visual_prompt or "").strip()
        if not vp:
            raise JobError(f"角色 {ch.name} 还没有外貌提示词，先写一个或用 AI 推荐造型")
        engine_name = portrait_engine(sv, proj.image_engine, payload.get("engine"),
                                     preset=proj.style_preset or "")
        try:
            engine = sv.engines.image(engine_name)
        except KeyError as e:
            raise JobError(str(e)) from None
        prompt = portrait_prompt(vp, style.anchor_text if style else "")
        negative = join_negative(proj.negative, style.negative if style else "", PORTRAIT_NEGATIVE)
        seed = shot_seed(proj.seed, char_id, ctx.job_id)
        slug, name = proj.slug, ch.name
    req = ImageRequest(prompt=prompt, negative=negative, aspect=portrait_aspect(engine),
                       seed=seed, prefix=f"studio/{slug}/portrait_{char_id}")
    call = engine.build(req)
    ctx.log(f"定妆照 {name} · {engine_name} · {seed_label(call, seed)}")
    files, run_dir = run_comfy(ctx, call, slug, req=req, what=f"定妆照 {name}")
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
                          manual=payload.get("manual") or {}, key=f"character.suggest:{char_id}",
                          step="character.suggest")
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
                          key=f"character.recommend:{episode_id}", step="character.recommend")
    have = set(existing)
    from backend.domain.characters import voice_only_description

    # 只出声、不出镜的人不用抽定妆照，不推荐
    chars = [c.model_dump() for c in out.characters if c.name.strip() not in have
             and not voice_only_description(c.description)][:count]
    ctx.log(f"推荐角色：{'、'.join(c['name'] for c in chars) or '（没有需要补的）'}")
    return {"characters": chars}


@HANDLERS.register("character.turnaround_look")
def run_turnaround_look(ctx, payload: dict) -> dict:
    """LLM 把外貌提示词整理成出设定图用的从头到脚全身描述，存进 character.turnaround_look。"""
    sv = ctx.services
    char_id = int(payload["character_id"])
    with sv.db.session() as s:
        ch = _load_char(s, char_id)
        if not (ch.visual_prompt or "").strip():
            raise JobError(f"角色 {ch.name} 还没有外貌提示词，先写一个或用 AI 推荐造型")
        proj, style = ch.project, ch.project.style_bible
        system, user = sv.prompts(proj.slug).render(
            "character_turnaround_look", project_title=proj.title, char_name=ch.name,
            description=ch.description, visual_prompt=ch.visual_prompt,
            core_features=ch.core_features,
            character_rules=style.character_rules if style else "")
        name = ch.name
    out = sv.llm.generate(LLM_TASK, system, user, TurnaroundLook, log=ctx.log,
                          manual=payload.get("manual") or {},
                          key=f"character.turnaround_look:{char_id}",
                          step="character.turnaround_look")
    with sv.db.session() as s:
        _load_char(s, char_id).turnaround_look = out.description
    n = len(out.description.split())
    ctx.log(f"{name} 的全身描述已写好（{n} 词）")
    return {"words": n}


def photo_look_problems(vp: str) -> list[str]:
    """看图写的外观：不能有中文（会被画成字），不写坐蹲跪这类姿势（定妆照是站着的半身）。"""
    from backend.pipeline.prompting import _POSE
    from backend.pipeline.storyboard import cjk_chars

    out = []
    bad = cjk_chars(vp)
    if bad:
        out.append(f"visual_prompt 里有中文「{bad[:20]}」，全部用英文写")
    poses = sorted({m.group(0).lower() for m in _POSE.finditer(vp or "")})
    if poses:
        out.append(f"visual_prompt 里写了姿势（{', '.join(poses)}），只写外观，不写坐、蹲、跪这类姿势")
    who = sorted({m.group(0).lower() for m in _IDENTITY.finditer(vp or "")})
    if who:
        out.append(f"visual_prompt 里写了「{', '.join(who)}」：不认人，不写像谁、明星、演员，"
                   "只描述看得见的脸型、五官、发型、体型和服装")
    return out


# 看图写外观时不认人：出现像谁 / 明星 / 演员这类词就让模型重写
_IDENTITY = re.compile(r"\b(?:resembl\w*|looks?\s+like|looking\s+like|celebrit\w*|actors?|"
                       r"actress(?:es)?|famous)\b", re.I)


@HANDLERS.register("character.from_photo")
def run_from_photo(ctx, payload: dict) -> dict:
    """LLM 看角色的参考照片，写英文外观提示词（只写外观）。结果记在参考照片上，等用户确认后填进外观提示词。
    文本模型看不了图时任务失败，提示到设置页换这一步的文本模型（或用手动粘贴）。"""
    sv = ctx.services
    char_id = int(payload["character_id"])
    with sv.db.session() as s:
        ch = _load_char(s, char_id)
        ref = appearance_ref_of(s, char_id)
        if ref is None:
            raise JobError(f"角色 {ch.name} 还没有上传参考照片，先在资料页上传一张")
        ref_id, ref_path = ref.id, sv.media.abs(ref.file_path)
        ref_url = "/media/" + quote(ref.file_path)
        proj, style = ch.project, ch.project.style_bible
        system, user = sv.prompts(proj.slug).render(
            "character_from_photo", project_title=proj.title, char_name=ch.name,
            description=ch.description, current_prompt=ch.visual_prompt,
            character_rules=style.character_rules if style else "")
        name = ch.name
    if not ref_path.is_file():
        raise JobError(f"参考照片文件不见了：{ref_path}，重新上传一张")

    def check(out: PhotoLook, *, last: bool = True) -> None:
        bad = photo_look_problems(out.visual_prompt)
        if bad and not last:
            raise ValueError("；".join(bad))

    out = sv.llm.generate(LLM_TASK, system, user, PhotoLook, check=check, log=ctx.log,
                          manual=payload.get("manual") or {},
                          key=f"character.from_photo:{char_id}", step="character.from_photo",
                          images=[ref_path], image_urls=[ref_url])
    look = {"visual_prompt": out.visual_prompt, "core_features": out.core_features,
            "job_id": ctx.job_id}
    with sv.db.session() as s:
        from backend.domain.models import Candidate

        cand = s.get(Candidate, ref_id)
        if cand is not None:
            set_photo_look(s, cand, look)
    n = len(out.visual_prompt.split())
    ctx.log(f"{name}：按参考照片写好外观（{n} 词），到角色资料页确认后填入")
    return {"visual_prompt": out.visual_prompt, "core_features": out.core_features,
            "candidate_id": ref_id}


def sheet_engine_name(sv, proj) -> str:
    """出设定图默认：设置页三视图这一步的设置 → H3（带定妆照锁脸）→ 项目的图像引擎。"""
    return sheet_engine(sv, proj.image_engine)


@HANDLERS.register("image.turnaround", gpu=True)
def run_turnaround_sheet(ctx, payload: dict) -> dict:
    """出一张三视图设定图（body：全身 4 视角 / face：头像 3 视角），引擎吃参考图时带选定的定妆照
    锁脸。这张被选定（第一张或原来的已过期）时立即切成单个视角存进各格。"""
    sv = ctx.services
    char_id = int(payload["character_id"])
    sheet = str(payload.get("sheet") or "")
    if sheet not in SHEETS:
        raise JobError(f"没有这种三视图设定图：{sheet}，可选：{', '.join(SHEETS)}")
    spec = SHEETS[sheet]
    with sv.db.session() as s:
        ch = _load_char(s, char_id)
        proj = ch.project
        engine_name = str(payload.get("engine") or sheet_engine_name(sv, proj))
        try:
            engine = sv.engines.image(engine_name)
        except KeyError as e:
            raise JobError(str(e)) from None
        portrait = portrait_of(s, char_id)
        refs = []
        if engine.caps.max_refs > 0:
            if portrait is None:
                raise JobError(f"角色 {ch.name} 还没有选定定妆照，{engine_name} 出三视图要用它锁脸")
            refs = [sv.media.abs(portrait.file_path)]
        if not look_of(ch):
            raise JobError(f"角色 {ch.name} 还没有外貌提示词，先写一个或用 AI 推荐造型")
        prompt, negative = sheet_inputs(ch, sheet)
        portrait_id = portrait.id if (portrait and refs) else None
        h = sheet_hash(sheet, look_of(ch), proj.style_bible.anchor_text if proj.style_bible else "",
                       negative, engine_name, portrait_id)
        seed = shot_seed(proj.seed, char_id, ctx.job_id)
        slug, name = proj.slug, ch.name
    req = ImageRequest(prompt=prompt, negative=negative, aspect=spec.aspect,
                       seed=seed, refs=refs,
                       prefix=f"studio/{slug}/turnaround_{char_id}_{sheet}")
    call = engine.build(req)
    ctx.log(f"三视图 {name} · {spec.label}设定图 · {engine_name} · {seed_label(call, seed)}"
            f"{' · 定妆照锁脸' if refs else ''}")
    files, run_dir = run_comfy(ctx, call, slug, {str(p): f"{name} 定妆照" for p in refs}, req=req,
                               what=f"三视图 {name} · {spec.label}",
                               ref_kinds={str(p): "定妆照" for p in refs})
    dst = sv.media.new_media_path(slug, "turnaround", files[0].suffix)
    shutil.copyfile(files[0], dst)
    with sv.db.session() as s:
        slot = get_or_create_slot(s, "character", char_id, sheet_slot_kind(sheet))
        # 自动选定规则同首帧：还没有选定或选定的已过期才换成这张
        c = add_candidate(s, slot, file_path=sv.media.rel(dst), engine=engine_name,
                          params={**call.meta, "sheet": sheet, "portrait_candidate_id": portrait_id,
                                  "workflow": sv.media.rel(run_dir / "workflow.api.json")},
                          input_hash=h, job_id=ctx.job_id)
        cid = c.id
        method = None
        if slot.selected_candidate_id == cid:
            method = crop_cells(s, sv.media, _load_char(s, char_id), sheet)
    if method == "equal":
        ctx.log(f"三视图 {name} · {spec.label}：没按人物找到 {len(spec.views)} 个人"
                f"（人数不对或背景不干净），按等宽切了，可以换一张或手动重新切图")
    ctx.log(f"三视图 {name} · {spec.label}设定图：候选 {cid}"
            f"{'，已切成 ' + str(len(spec.views)) + ' 格' if method else '（没有选定，未切图）'}")
    return {"candidate_ids": [cid], "file": sv.media.rel(dst), "crop": method}
