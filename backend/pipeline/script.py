"""剧本阶段：结构（段落、角色、场景）→ 视觉（风格锚、角色与场景外观）。

每一步记录输入指纹（只算数据）和模板规则版本，输入没变就跳过，中途失败可以断点续跑。
"""
from __future__ import annotations

import math
import re
import unicodedata

from sqlalchemy import select

from backend.domain.characters import portrait_of, voice_only_description
from backend.domain.hashing import input_hash
from backend.domain.locations import location_image_of
from backend.domain.models import Character, Location, SceneBlock, StyleBible
from backend.domain.style_presets import prompt_vars
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
#   v3（结构）：爆款剧本规范（3 秒钩子、冲突、反转、结尾钩子、对白密度和长度、对白格式）、风格预设
#   v3（视觉）：风格锚按项目的风格预设定画风方向，负面词参考预设
#   v4（结构、视觉）：开头的称呼「AI 漫剧」改成「AI 短剧」（真人写实预设不是漫画）
#   v5（视觉）：只出声、不出镜的角色 visual_prompt 只写性别年龄族裔加 voice only（约 10 词）
STRUCTURE_RULES_VERSION = 4
VISUAL_RULES_VERSION = 5
SCRIPT_RULES_OUTDATED_HINT = "剧本规则已更新，要按新规则重出请强制重跑（会重写段落、牵连镜头）"
DEFAULT_SHOT_SECONDS = 5.0


def structure_rules(prompts) -> str:
    return template_rules(prompts, "script_structure", STRUCTURE_RULES_VERSION)


def visual_rules(prompts) -> str:
    return template_rules(prompts, "script_visual", VISUAL_RULES_VERSION)


def shot_plan(engines, video_engine: str, target_seconds: float,
              pace: float | None = None) -> tuple[int, float]:
    """(本集大约几个镜头, 每镜几秒)：和分镜阶段同一个算法（目标时长 ÷ 每镜秒数）。每镜秒数：项目设了
    镜头节奏（pace）就用它，否则用视频引擎的甜区；引擎不存在时按 5 秒估（剧本阶段不该因为视频引擎没配好
    而失败）。"""
    from backend.pipeline.storyboard import pace_seconds

    try:
        caps = engines.video(video_engine).caps
        seconds = float(pace_seconds(pace, caps))
    except (KeyError, AttributeError):
        seconds = float(pace) if pace else DEFAULT_SHOT_SECONDS
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
                        aspect: str = "9:16", preset: str | None = None) -> tuple[str, str, str]:
    """剧本结构这次要用的提示词及其输入指纹（不调用 LLM）。script 阶段和重跑影响预估共用。
    指纹见 structure_inputs_fp（不含修改意见、不含模板文字）。"""
    if not shot_count:
        shot_count = shot_budget(target_seconds, shot_seconds)
    system, user, _, _ = render_with_note(prompts, "script_structure", note,
                                          project_title=project_title,
                                          target_seconds=target_seconds, source_text=source_text,
                                          shot_count=shot_count, shot_seconds=shot_seconds,
                                          aspect=aspect or "9:16", **prompt_vars(preset))
    return system, user, structure_inputs_fp(project_title, target_seconds, source_text)


def visual_prompt_fp(prompts, project_title: str, characters: list[dict], locations: list[dict],
                     excerpt: str, note: str | None = None,
                     preset: str | None = None) -> tuple[str, str, str]:
    """视觉设定这次要用的提示词及其输入指纹（不调用 LLM）。script 阶段和重跑影响预估共用。
    风格预设只进提示词、不进指纹：换预设不自动重写视觉设定，要重新生成才按新预设写。"""
    system, user, _, _ = render_with_note(prompts, "script_visual", note,
                                          project_title=project_title, characters=characters,
                                          locations=locations, excerpt=excerpt,
                                          **prompt_vars(preset))
    return system, user, visual_inputs_fp(project_title, characters, locations, excerpt)


def structure_inputs(sv, ep, proj, note: str | None = None) -> tuple[str, str, str]:
    """按剧集当前状态渲染剧本结构提示词（带镜头预算），script 阶段和影响预估共用。"""
    count, seconds = shot_plan(sv.engines, proj.video_engine, ep.target_seconds,
                               getattr(proj, "shot_seconds", None))
    return structure_prompt_fp(sv.prompts(proj.slug), proj.title, ep.target_seconds,
                               ep.source_text, note, shot_count=count, shot_seconds=seconds,
                               aspect=proj.aspect or "9:16", preset=proj.style_preset)


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
    # 只出声的角色才多一个 voice_only 键：其余角色的写法和以前一样，老剧集的指纹不变
    chars = [{"name": c.name, "description": c.description,
              **({"voice_only": True} if getattr(c, "voice_only", False) else {})}
             for c in proj.characters]
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
        target, source = float(ep.target_seconds), ep.source_text or ""

    def check(out: ScriptStructure, *, last: bool = True) -> None:
        bad = dialogue_problems([sc.text for sc in out.scenes], target, source)
        if bad and not last:   # 最后一次机会不为对白写法让整段失败
            raise ValueError("；".join(bad))
        if bad:
            ctx.log(f"剧本结构：对白没完全达到爆款规范（不再重写）：{'；'.join(bad)}")

    out = sv.llm.generate("script", system, user, ScriptStructure, check=check, log=ctx.log,
                          manual=manual, key="script_structure")
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
            if voice_only_description(c.description):
                ch.voice_only = True   # 只出声、不出镜：不抽定妆照，分镜不给占位符（可在角色卡上改）
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


# 爆款剧本规范的对白校验。段落里的对白按「角色名：「台词」」写（模板要求），能认出来才校验：
# 单句不超过 16 个字（和分镜的台词上限一致），全集每 5 秒约 1 句（留 30% 余量）。
# 素材里本来就没有对白、模型也没写对白时不卡（无声的片子）；素材有对白、段落里却一句都认不出来，
# 多半是没按格式写，让它重写。
DIALOGUE_MAX_CHARS = 16
DIALOGUE_SECONDS_PER_LINE = 5.0
DIALOGUE_DENSITY_SLACK = 0.7
_DIALOGUE = re.compile(r"[:：]\s*[「“\"]([^」”\"\n]*)[」”\"]")
_SOURCE_SPEECH = re.compile(r"[「“\"][^」”\"\n]+[」”\"]|[说道问喊][:：]")


# 冒号前是屏幕 / 字条 / 标牌这类「画面里的字」时不是台词（「手机屏幕显示：「余额不足」」）
_SCREEN_TEXT = re.compile(r"(?:屏幕|屏|字条|纸条|便条|显示|写着|写道|标牌|招牌|牌子|告示|横幅|字幕|"
                          r"短信|消息|弹幕|标语|海报|弹窗|通知)[上里中]?\s*$")
_CLAUSE_END = re.compile(r"[。！？!?\n」”\"]")


def _is_screen_text(text: str, colon: int) -> bool:
    head = _CLAUSE_END.split(text[:colon])[-1]
    return bool(_SCREEN_TEXT.search(head))


def dialogue_lines(texts: list[str]) -> list[str]:
    return [m.group(1).strip() for t in texts for m in _DIALOGUE.finditer(t or "")
            if m.group(1).strip() and not _is_screen_text(t, m.start())]


def _speech_len(line: str) -> int:
    """台词字数：不算标点和空白（「好……走！」是 2 个字）。"""
    return sum(1 for ch in line if not ch.isspace()
               and not unicodedata.category(ch).startswith(("P", "S", "Z")))


def min_dialogue_lines(target_seconds: float) -> int:
    return max(1, math.floor(target_seconds / DIALOGUE_SECONDS_PER_LINE * DIALOGUE_DENSITY_SLACK))


def dialogue_problems(texts: list[str], target_seconds: float, source_text: str = "") -> list[str]:
    lines = dialogue_lines(texts)
    out = []
    if not lines:
        if _SOURCE_SPEECH.search(source_text or ""):
            out.append("段落里认不出对白：对白要写成 角色名：「台词」（全角冒号加直角引号，一句一对引号）")
        return out
    long = [x for x in lines if _speech_len(x) > DIALOGUE_MAX_CHARS]
    if long:
        shown = "、".join(f"「{x[:24]}」（{_speech_len(x)} 字）" for x in long[:4])
        out.append(f"有 {len(long)} 句台词超过 {DIALOGUE_MAX_CHARS} 个字：{shown}；"
                   "拆成几句短的口语，或删掉书面的部分，不写长独白")
    need = min_dialogue_lines(target_seconds)
    if len(lines) < need:
        out.append(f"全集只有 {len(lines)} 句台词，{int(target_seconds)} 秒的一集至少要 {need} 句"
                   f"（约每 {DIALOGUE_SECONDS_PER_LINE:g} 秒 1 句）；让人物多说短句推动剧情，"
                   "台词和动作放在同一个节拍里，不要超出镜头预算")
    return out


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
                                            ep.source_text[:4000], note,
                                            preset=proj.style_preset)
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
        kept_locs: list[str] = []
        for lv in out.locations:
            loc = by_loc.get(lv.name)
            if loc is None:
                continue
            # 已经选定了场景图：外观提示词不覆盖（覆盖了场景图就过期，这个场景所有镜头的首帧跟着过期）
            if (loc.visual_prompt or "").strip() and location_image_of(s, loc.id) is not None:
                kept_locs.append(loc.name)
                continue
            loc.visual_prompt = lv.visual_prompt
        sb = proj.style_bible
        if sb is None:
            sb = StyleBible()
            s.add(sb)
            proj.style_bible = sb
        st = out.style
        # 已经有风格锚、这次又没带修改意见：整套风格不覆盖。锚在所有首帧、三视图、定妆照的输入里，
        # LLM 每次写的锚都不一样——改一个角色描述触发的重跑会让全片首帧过期。只补空着的字段。
        keep_style = bool((sb.anchor_text or "").strip()) and not (note or "").strip()
        new = {"anchor_text": st.anchor_text, "palette": st.palette, "lighting": st.lighting,
               "texture": st.texture, "character_rules": st.character_rules,
               "mood_keywords": list(st.mood_keywords), "negative": st.negative}
        for field, value in new.items():
            if not keep_style or not getattr(sb, field):
                setattr(sb, field, value)
        ep.stage_hashes = {**(ep.stage_hashes or {}), "visual": script_record(fp, rules)}
    if kept:
        ctx.log(f"视觉设定：{'、'.join(kept)} 已经选定了定妆照造型，外貌提示词不覆盖"
                "（要换造型到角色抽屉里改）")
    if kept_locs:
        ctx.log(f"视觉设定：{'、'.join(kept_locs)} 已经选定了场景图，场景外观不覆盖"
                "（要换到「设定」里改，再重出场景图）")
    if keep_style:
        ctx.log("视觉设定：已有风格锚，没覆盖；要换风格请带修改意见重新生成")
    ctx.log(f"视觉设定：风格锚 {len(out.style.anchor_text.split())} 词，"
            f"角色 {len(out.characters)}，场景 {len(out.locations)}")
    return "done"
