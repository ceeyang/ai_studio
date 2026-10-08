"""剧本阶段：结构（段落、角色、场景）→ 视觉（风格锚、角色与场景外观）。

每一步记录输入指纹（渲染后提示词的哈希），输入没变就跳过，中途失败可以断点续跑。
"""
from __future__ import annotations

from sqlalchemy import select

from backend.domain.hashing import input_hash
from backend.domain.models import Character, Location, SceneBlock, StyleBible
from backend.llm.schemas import ScriptStructure, ScriptVisual
from backend.pipeline.common import continue_chain, load_episode
from backend.queue.errors import JobError
from backend.queue.handlers import HANDLERS


@HANDLERS.register("script")
def run_script(ctx, payload: dict) -> dict:
    episode_id = int(payload["episode_id"])
    force = bool(payload.get("force"))
    manual = payload.get("manual") or {}
    structure = _structure(ctx, episode_id, force, manual)
    visual = _visual(ctx, episode_id, force, manual)
    continue_chain(ctx, episode_id, payload)
    return {"structure": structure, "visual": visual}


def _structure(ctx, episode_id: int, force: bool, manual: dict) -> str:
    sv = ctx.services
    with sv.db.session() as s:
        ep, proj = load_episode(s, episode_id)
        if not ep.source_text.strip():
            raise JobError("这一集还没有原文或梗概，先填写 source_text")
        system, user = sv.prompts(proj.slug).render(
            "script_structure", project_title=proj.title, target_seconds=ep.target_seconds,
            source_text=ep.source_text)
        fp = input_hash("structure", system, user)
        if not force and (ep.stage_hashes or {}).get("structure") == fp and ep.scene_blocks:
            ctx.log("剧本结构：输入没变，跳过")
            return "skipped"
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
        ep.stage_hashes = {**(ep.stage_hashes or {}), "structure": fp}
        ep.status = "scripted"
    ctx.log(f"剧本结构：{len(out.scenes)} 段，角色 {len(out.characters)} 个，"
            f"场景 {len(out.locations)} 个")
    return "done"


def _visual(ctx, episode_id: int, force: bool, manual: dict) -> str:
    sv = ctx.services
    with sv.db.session() as s:
        ep, proj = load_episode(s, episode_id)
        chars = [{"name": c.name, "description": c.description} for c in proj.characters]
        locs = [{"name": l.name, "time_of_day": l.time_of_day, "atmosphere": l.atmosphere}
                for l in proj.locations]
        system, user = sv.prompts(proj.slug).render(
            "script_visual", project_title=proj.title, characters=chars, locations=locs,
            excerpt=ep.source_text[:4000])
        fp = input_hash("visual", system, user)
        if not force and (ep.stage_hashes or {}).get("visual") == fp and proj.style_bible_id:
            ctx.log("视觉设定：输入没变，跳过")
            return "skipped"
    char_names = {c["name"] for c in chars}
    loc_names = {l["name"] for l in locs}

    def check(out: ScriptVisual) -> None:
        missing_c = char_names - {c.name for c in out.characters}
        missing_l = loc_names - {l.name for l in out.locations}
        if missing_c:
            raise ValueError(f"缺少角色：{'、'.join(sorted(missing_c))}")
        if missing_l:
            raise ValueError(f"缺少场景：{'、'.join(sorted(missing_l))}")

    out = sv.llm.generate("script", system, user, ScriptVisual, check=check, log=ctx.log,
                          manual=manual, key="script_visual")
    with sv.db.session() as s:
        ep, proj = load_episode(s, episode_id)
        by_char = {c.name: c for c in proj.characters}
        for cv in out.characters:
            if cv.name in by_char:
                by_char[cv.name].visual_prompt = cv.visual_prompt
                by_char[cv.name].core_features = cv.core_features
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
        ep.stage_hashes = {**(ep.stage_hashes or {}), "visual": fp}
    ctx.log(f"视觉设定：风格锚 {len(out.style.anchor_text.split())} 词，"
            f"角色 {len(out.characters)}，场景 {len(out.locations)}")
    return "done"
