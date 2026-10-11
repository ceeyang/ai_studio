"""剧本阶段：结构（段落、角色、场景）→ 视觉（风格锚、角色与场景外观）。

每一步记录输入指纹（只算数据）和模板规则版本，输入没变就跳过，中途失败可以断点续跑。
"""
from __future__ import annotations

from sqlalchemy import select

from backend.domain.characters import portrait_of
from backend.domain.hashing import input_hash
from backend.domain.models import Character, Location, SceneBlock, StyleBible
from backend.llm.schemas import ScriptStructure, ScriptVisual
from backend.pipeline.common import (clean_note, continue_chain, load_episode, render_with_note,
                                     template_rules)
from backend.pipeline.storyboard import judge_block, shot_budget
from backend.queue.errors import JobError
from backend.queue.handlers import HANDLERS

# 剧本模板（script_structure.md / script_visual.md）的规则版本：改了模板里的写法要求就加一。
# 指纹只算数据（原文、时长、角色表……），模板文字不进指纹：版本不同只提示「规则已更新」、不自动重跑。
# 以前的指纹是整段渲染后提示词的哈希（字符串），模板一改就全对不上，一次不强制的整集运行就会
# 重写剧本结构、清空段落、牵连所有镜头。
STRUCTURE_RULES_VERSION = 2
VISUAL_RULES_VERSION = 2
SCRIPT_RULES_OUTDATED_HINT = "剧本规则已更新，要按新规则重出请强制重跑（会重写段落、牵连镜头）"
DEFAULT_SHOT_SECONDS = 5.0


def structure_rules(prompts) -> str:
    return template_rules(prompts, "script_structure", STRUCTURE_RULES_VERSION)


def visual_rules(prompts) -> str:
    return template_rules(prompts, "script_visual", VISUAL_RULES_VERSION)


def shot_plan(engines, video_engine: str, target_seconds: float) -> tuple[int, float]:
    """(本集大约几个镜头, 每镜几秒)：和分镜阶段同一个算法（目标时长 ÷ 视频引擎甜区）。
    引擎不存在时按 5 秒估（剧本阶段不该因为视频引擎没配好而失败）。"""
    try:
        seconds = float(engines.video(video_engine).caps.default_seconds)
    except (KeyError, AttributeError):
        seconds = DEFAULT_SHOT_SECONDS
    return shot_budget(target_seconds, seconds), seconds


def structure_inputs_fp(project_title: str, target_seconds: float, source_text: str) -> str:
    """剧本结构的输入指纹：只算数据。镜头预算和画幅只是写法提示，不进指纹——换个视频引擎不该让
    整集剧本重写（段落一清空，所有镜头都没了）。"""
    return input_hash("structure-inputs", project_title or "", float(target_seconds),
                      source_text or "")


def visual_inputs_fp(project_title: str, characters: list[dict], locations: list[dict],
                     excerpt: str) -> str:
    return input_hash("visual-inputs", project_title or "", characters, locations, excerpt or "")


def structure_prompt_fp(prompts, project_title: str, target_seconds: float,
                        source_text: str, note: str | None = None, *, shot_count: int = 0,
                        shot_seconds: float = DEFAULT_SHOT_SECONDS,
                        aspect: str = "9:16") -> tuple[str, str, str]:
    """剧本结构这次要用的提示词及其输入指纹（不调用 LLM）。script 阶段和重跑影响预估共用。
    指纹见 structure_inputs_fp（不含修改意见、不含模板文字）。"""
    if not shot_count:
        shot_count = shot_budget(target_seconds, shot_seconds)
    system, user, _, _ = render_with_note(prompts, "script_structure", note,
                                          project_title=project_title,
                                          target_seconds=target_seconds, source_text=source_text,
                                          shot_count=shot_count, shot_seconds=shot_seconds,
                                          aspect=aspect or "9:16")
    return system, user, structure_inputs_fp(project_title, target_seconds, source_text)


def visual_prompt_fp(prompts, project_title: str, characters: list[dict], locations: list[dict],
                     excerpt: str, note: str | None = None) -> tuple[str, str, str]:
    """视觉设定这次要用的提示词及其输入指纹（不调用 LLM）。script 阶段和重跑影响预估共用。"""
    system, user, _, _ = render_with_note(prompts, "script_visual", note,
                                          project_title=project_title, characters=characters,
                                          locations=locations, excerpt=excerpt)
    return system, user, visual_inputs_fp(project_title, characters, locations, excerpt)


def structure_inputs(sv, ep, proj, note: str | None = None) -> tuple[str, str, str]:
    """按剧集当前状态渲染剧本结构提示词（带镜头预算），script 阶段和影响预估共用。"""
    count, seconds = shot_plan(sv.engines, proj.video_engine, ep.target_seconds)
    return structure_prompt_fp(sv.prompts(proj.slug), proj.title, ep.target_seconds,
                               ep.source_text, note, shot_count=count, shot_seconds=seconds,
                               aspect=proj.aspect or "9:16")


def judge_script(record, fp: str, rules: str, has_output: bool):
    """剧本结构 / 视觉设定的记录判断，和分镜同一套（judge_block）：新格式 {"inputs", "rules"}；
    旧格式（整段提示词哈希的字符串）有产物时按没变处理，由调用方换成新格式，不重跑。"""
    return judge_block(record, fp, rules, has_output)


def script_record(fp: str, rules: str | None) -> dict:
    return {"inputs": fp, "rules": rules}


def visual_chars_and_locations(proj) -> tuple[list[dict], list[dict]]:
    """视觉设定提示词要用的角色表、场景表（当前数据库里的样子）。

    script 阶段和重跑影响预估共用，避免两边各自拼一遍而对不上。
    """
    chars = [{"name": c.name, "description": c.description} for c in proj.characters]
    locs = [{"name": l.name, "time_of_day": l.time_of_day, "atmosphere": l.atmosphere}
            for l in proj.locations]
    return chars, locs


@HANDLERS.register("script")
def run_script(ctx, payload: dict) -> dict:
    episode_id = int(payload["episode_id"])
    note = clean_note(payload.get("note"))
    force = bool(payload.get("force")) or note is not None  # 带修改意见就一定重跑
    manual = payload.get("manual") or {}
    structure = _structure(ctx, episode_id, force, manual, note)
    visual = _visual(ctx, episode_id, force, manual, note)
    continue_chain(ctx, episode_id, payload)
    return {"structure": structure, "visual": visual}


def _skip(ctx, ep, key: str, label: str, fp: str, judged) -> str:
    if judged.legacy:
        ep.stage_hashes = {**(ep.stage_hashes or {}), key: script_record(fp, None)}
        ctx.log(f"{label}：旧版记录，无法判断有没有变，先不动（已换成新格式）；要重出请用重新生成")
    elif judged.rules_outdated:
        ctx.log(f"{label}：输入没变，跳过；{SCRIPT_RULES_OUTDATED_HINT}")
    else:
        ctx.log(f"{label}：输入没变，跳过")
    return "skipped"


def _structure(ctx, episode_id: int, force: bool, manual: dict, note: str | None = None) -> str:
    sv = ctx.services
    with sv.db.session() as s:
        ep, proj = load_episode(s, episode_id)
        if not ep.source_text.strip():
            raise JobError("这一集还没有原文或梗概，先填写 source_text")
        system, user, fp = structure_inputs(sv, ep, proj, note)
        rules = structure_rules(sv.prompts(proj.slug))
        judged = judge_script((ep.stage_hashes or {}).get("structure"), fp, rules,
                              bool(ep.scene_blocks))
        if judged.unchanged and not force:
            return _skip(ctx, ep, "structure", "剧本结构", fp, judged)
    out = sv.llm.generate("script", system, user, ScriptStructure, log=ctx.log, manual=manual,
                          key="script_structure")
    with sv.db.session() as s:
        ep, proj = load_episode(s, episode_id)
        for c in out.characters:
            ch = s.scalar(select(Character).where(Character.project_id == proj.id,
                                                  Character.name == c.name))
            if ch is None:
                ch = Character(project_id=proj.id, name=c.name)
                s.add(ch)
            if c.description:
                ch.description = c.description
        locs: dict[str, Location] = {}
        for lo in out.locations:
            loc = s.scalar(select(Location).where(Location.project_id == proj.id,
                                                  Location.name == lo.name))
            if loc is None:
                loc = Location(project_id=proj.id, name=lo.name)
                s.add(loc)
            loc.time_of_day = lo.time_of_day or loc.time_of_day
            loc.atmosphere = lo.atmosphere or loc.atmosphere
            locs[lo.name] = loc
        s.flush()
        ep.scene_blocks.clear()  # 旧段落删掉；镜头的 scene_block_id 由外键置空
        s.flush()
        for i, sc in enumerate(out.scenes):
            ep.scene_blocks.append(SceneBlock(order=i, location_id=locs[sc.location].id,
                                              text=sc.text))
        if not ep.title:
            ep.title = out.title
        ep.stage_hashes = {**(ep.stage_hashes or {}), "structure": script_record(fp, rules)}
        ep.status = "scripted"
    ctx.log(f"剧本结构：{len(out.scenes)} 段，角色 {len(out.characters)} 个，"
            f"场景 {len(out.locations)} 个")
    return "done"


# 风格锚只写成像风格。锚会拼到每个首帧前面：写进雨、灯泡、工具，室内镜头也会下雨，
# 视频里一半词是道具。出现场景 / 天气 / 时间 / 具体光源 / 道具词就让模型重写（最后一次机会不卡）。
ANCHOR_WORDS = (30, 90)


def anchor_problems(anchor: str) -> list[str]:
    from backend.pipeline.prompting import _SCENE_TERMS

    out = []
    n = len((anchor or "").split())
    if not ANCHOR_WORDS[0] <= n <= ANCHOR_WORDS[1]:
        out.append(f"style.anchor_text 要 40–70 个英文单词，现在是 {n} 个")
    hits = sorted({m.group(0).strip().lower() for m in _SCENE_TERMS.finditer(anchor or "")})
    if hits:
        out.append(f"style.anchor_text 里写了场景/天气/时间/光源/道具：{', '.join(hits[:8])}；"
                   "锚只写全剧通用的成像风格（画风、镜头与胶片质感、色彩倾向和对比、光线软硬、"
                   "皮肤与材质的真实感），这些具体内容写进场景的 visual_prompt")
    return out


def _visual(ctx, episode_id: int, force: bool, manual: dict, note: str | None = None) -> str:
    sv = ctx.services
    with sv.db.session() as s:
        ep, proj = load_episode(s, episode_id)
        chars, locs = visual_chars_and_locations(proj)
        prompts = sv.prompts(proj.slug)
        system, user, fp = visual_prompt_fp(prompts, proj.title, chars, locs,
                                            ep.source_text[:4000], note)
        rules = visual_rules(prompts)
        judged = judge_script((ep.stage_hashes or {}).get("visual"), fp, rules,
                              bool(proj.style_bible_id))
        if judged.unchanged and not force:
            return _skip(ctx, ep, "visual", "视觉设定", fp, judged)
    char_names = {c["name"] for c in chars}
    loc_names = {l["name"] for l in locs}

    def check(out: ScriptVisual, *, last: bool = True) -> None:
        missing_c = char_names - {c.name for c in out.characters}
        missing_l = loc_names - {l.name for l in out.locations}
        if missing_c:
            raise ValueError(f"缺少角色：{'、'.join(sorted(missing_c))}")
        if missing_l:
            raise ValueError(f"缺少场景：{'、'.join(sorted(missing_l))}")
        bad = anchor_problems(out.style.anchor_text)
        if bad and not last:   # 最后一次机会不为锚的写法让整段失败
            raise ValueError("；".join(bad))

    out = sv.llm.generate("script", system, user, ScriptVisual, check=check, log=ctx.log,
                          manual=manual, key="script_visual")
    kept: list[str] = []
    with sv.db.session() as s:
        ep, proj = load_episode(s, episode_id)
        by_char = {c.name: c for c in proj.characters}
        for cv in out.characters:
            ch = by_char.get(cv.name)
            if ch is None:
                continue
            # 已经按定妆照定了造型：外貌提示词不覆盖（覆盖了定妆照和外貌文字就对不上），只补空的
            if (ch.visual_prompt or "").strip() and portrait_of(s, ch.id) is not None:
                kept.append(ch.name)
                if not (ch.core_features or "").strip():
                    ch.core_features = cv.core_features
                continue
            ch.visual_prompt = cv.visual_prompt
            ch.core_features = cv.core_features
        by_loc = {l.name: l for l in proj.locations}
        for lv in out.locations:
            if lv.name in by_loc:
                by_loc[lv.name].visual_prompt = lv.visual_prompt
        sb = proj.style_bible
        if sb is None:
            sb = StyleBible()
            s.add(sb)
            proj.style_bible = sb
        st = out.style
        sb.anchor_text, sb.palette, sb.lighting = st.anchor_text, st.palette, st.lighting
        sb.texture, sb.character_rules = st.texture, st.character_rules
        sb.mood_keywords, sb.negative = list(st.mood_keywords), st.negative
        ep.stage_hashes = {**(ep.stage_hashes or {}), "visual": script_record(fp, rules)}
    if kept:
        ctx.log(f"视觉设定：{'、'.join(kept)} 已经选定了定妆照造型，外貌提示词不覆盖"
                "（要换造型到角色抽屉里改）")
    ctx.log(f"视觉设定：风格锚 {len(out.style.anchor_text.split())} 词，"
            f"角色 {len(out.characters)}，场景 {len(out.locations)}")
    return "done"
