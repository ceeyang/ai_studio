"""分镜阶段：按目标时长和引擎甜区算镜头预算，按剧本段落分配，逐段让 LLM 出镜头。"""
from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from backend.domain.hashing import input_hash
from backend.domain.models import Episode, Shot
from backend.domain.shots import renumber_shots
from backend.domain.slots import delete_slots
from backend.llm.schemas import StoryboardOut
from backend.llm.gateway import MAX_RETRIES
from backend.pipeline.common import clean_note, continue_chain, load_episode, render_with_note
from backend.queue.errors import JobError
from backend.queue.handlers import HANDLERS


def shot_budget(target_seconds: float, shot_seconds: float) -> int:
    return max(1, round(target_seconds / max(shot_seconds, 0.1)))


def allocate_shots(total: int, weights: list[float]) -> list[int]:
    n = len(weights)
    if n == 0:
        return []
    remaining = max(total, n) - n  # 每段先保底 1 个
    w = [max(float(x), 0.0) for x in weights]
    wsum = sum(w)
    shares = [remaining * x / wsum for x in w] if wsum > 0 else [remaining / n] * n
    floors = [int(x) for x in shares]
    rest = remaining - sum(floors)
    order = sorted(range(n), key=lambda i: (-(shares[i] - floors[i]), i))
    for i in order[:rest]:
        floors[i] += 1
    return [1 + f for f in floors]


# ------------------------------------------------------------ 质量规则
# 英文提示词里混进中文（多半是角色名）时，图像模型会把它当成要画的字；中日韩字符和全角标点都不允许。
_CJK = re.compile(r"[\u2e80-\u2fff\u3000-\u303f\u3040-\u30ff\u3100-\u31ff\u3400-\u4dbf"
                  r"\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff\ufe30-\ufe4f\uff00-\uffef]+")
DURATION_TOLERANCE = 0.10   # 每段镜头总时长和预算的允许偏差（±10%），整集因此也在目标的 ±10% 内
MIN_SHOT_SECONDS = 2.0      # 和 ShotOut.duration 的下限一致
DIALOGUE_TAIL = 0.4         # 最后一句台词说完后留的余量
MAX_SHRINK = 0.25           # 要按比例砍掉超过 25% 的时长时让 LLM 重写，不机械缩放


# 双引号里的是画面上要出现的字（如 a sign reading "老周修车"），允许中文
_QUOTED = re.compile(r'"[^"\n]*"|“[^”\n]*”|「[^」\n]*」')


def cjk_chars(text: str) -> str:
    return "".join(_CJK.findall(text or ""))


def cjk_outside_quotes(text: str) -> str:
    return cjk_chars(_QUOTED.sub(" ", text or ""))


def cjk_problems(shots) -> list[str]:
    out = []
    for i, sh in enumerate(shots, start=1):
        for field in ("image_prompt", "motion_prompt", "audio_prompt"):
            bad = cjk_outside_quotes(getattr(sh, field, "") or "")
            if bad:
                out.append(f"第 {i} 镜 {field} 里有中文「{bad[:20]}」")
        bad = "".join(cjk_outside_quotes(p) for p in (getattr(sh, "props", None) or []))
        if bad:
            out.append(f"第 {i} 镜 props 里有中文「{bad[:20]}」")
    return out


def block_seconds(target_seconds: float, count: int, total_count: int) -> float:
    """这一段镜头总时长的预算：按镜头数分整集目标时长。"""
    return round(float(target_seconds) * count / max(total_count, 1), 1)


def fit_durations(durs: list[float], mins: list[float], target: float, *, hi: float
                  ) -> list[float]:
    """按比例把镜头时长缩放到总和约等于 target，每镜不低于 mins[i]、不高于 hi（引擎上限）。
    碰到上下限的镜头固定住，剩下的继续按比例分（注水法）。"""
    n = len(durs)
    lo = [min(max(m, MIN_SHOT_SECONDS), hi) for m in mins]
    out: list[float] = [0.0] * n
    free = set(range(n))
    while free:
        fixed = sum(out[i] for i in range(n) if i not in free)
        base = sum(durs[i] for i in free)
        f = (target - fixed) / base if base > 0 else 1.0
        viol = [i for i in free if durs[i] * f < lo[i] or durs[i] * f > hi]
        if not viol:
            for i in free:
                out[i] = durs[i] * f
            break
        for i in viol:
            out[i] = lo[i] if durs[i] * f < lo[i] else hi
            free.discard(i)
    return [round(float(x), 1) for x in out]


def shot_min_seconds(sh) -> float:
    """缩短时不能切掉台词：最后一句说完再留 0.4 秒（LLM 本来就没留够的，不往长了拉）。"""
    end = max((d.end for d in sh.dialogue), default=0.0)
    if not end:
        return MIN_SHOT_SECONDS
    return max(MIN_SHOT_SECONDS, min(float(sh.duration), end + DIALOGUE_TAIL))


def budget_durations(shots, target: float, max_seconds: float, *, rewrite_over: bool = True
                     ) -> tuple[list[float], str | None]:
    """把一段镜头的时长落进预算 target（±10%）。返回 (时长, 错误)：

    - 偏差在 ±10% 内不动；
    - 要缩短超过 25%（rewrite_over=True 时）给错误让 LLM 重新分配——机械地按比例砍会把节奏砍乱；
    - 缩放也落不进（台词太长）给错误，让 LLM 重写；
    - 引擎上限凑不够预算不算错（不是 LLM 能改的）。"""
    durs = [min(float(sh.duration), max_seconds) for sh in shots]
    tol = max(target * DURATION_TOLERANCE, 0.5)
    total_in = sum(durs)
    if abs(total_in - target) <= tol:
        return durs, None
    if rewrite_over and total_in > 0 and 1 - target / total_in > MAX_SHRINK:
        return durs, (f"这一段镜头 duration 加起来 {total_in:.1f} 秒，本段预算 {target:.1f} 秒"
                      f"（±{tol:.1f} 秒），要砍掉 {(1 - target / total_in) * 100:.0f}% 才放得下；"
                      "请重新分配每镜时长（必要时减少台词、合并动作），让 duration 加起来落在预算内")
    fitted = fit_durations(durs, [shot_min_seconds(sh) for sh in shots], target, hi=max_seconds)
    total = sum(fitted)
    if total > target + tol:
        return fitted, (f"这一段镜头总时长 {sum(float(sh.duration) for sh in shots):.1f} 秒，"
                        f"要在 {target:.1f} 秒左右（±{tol:.1f} 秒）；按台词缩短后最少还要 {total:.1f} 秒，"
                        "请减少台词或合并镜头，让 duration 加起来落在预算内")
    return fitted, None


def _last_action(db, block_id: int) -> str:
    with db.session() as s:
        sh = s.scalar(select(Shot).where(Shot.scene_block_id == block_id)
                      .order_by(Shot.order.desc(), Shot.id.desc()))
        return sh.action if sh else ""


@dataclass
class StoryboardPlan:
    """跑分镜要用到的输入：预算分好的每段镜头数、风格锚、角色表、场景表。"""
    ep: Episode
    shot_seconds: float
    max_seconds: float
    anchor: str
    characters: list[dict]
    char_ids: dict[str, int]
    blocks: list[dict]   # [{"id", "order", "text", "location", "count", "seconds"}]，按段落顺序
    prompts: object       # PromptLibrary


def build_storyboard_plan(s: Session, sv, episode_id: int) -> StoryboardPlan:
    """算出这一集跑分镜要用的段落计划（预算、风格锚、角色表、场景表），不调用 LLM。

    run_storyboard 真正跑的时候、重跑影响预估估算的时候都走这一个函数拿输入，
    避免两边各自拼一遍 anchor/characters/locs/counts 而慢慢对不上。
    """
    ep, proj = load_episode(s, episode_id)
    blocks = list(ep.scene_blocks)
    if not blocks:
        raise JobError("这一集还没有剧本段落，先跑 script")
    try:
        caps = sv.engines.video(proj.video_engine).caps
    except KeyError as e:
        raise JobError(str(e)) from None
    shot_seconds, max_seconds = caps.default_seconds, caps.max_seconds
    counts = allocate_shots(shot_budget(ep.target_seconds, shot_seconds),
                            [len(b.text) for b in blocks])
    total_count = sum(counts)
    anchor = proj.style_bible.anchor_text if proj.style_bible else ""
    characters = [{"name": c.name, "visual_prompt": c.visual_prompt or c.description}
                 for c in proj.characters]
    char_ids = {c.name: c.id for c in proj.characters}
    locs = {l.id: {"name": l.name, "time_of_day": l.time_of_day, "atmosphere": l.atmosphere,
                   "visual_prompt": l.visual_prompt} for l in proj.locations}
    plan = [{"id": b.id, "order": b.order, "text": b.text, "location": locs.get(b.location_id),
             "count": n, "seconds": block_seconds(ep.target_seconds, n, total_count)}
            for b, n in zip(blocks, counts)]
    return StoryboardPlan(ep=ep, shot_seconds=shot_seconds, max_seconds=max_seconds, anchor=anchor,
                          characters=characters, char_ids=char_ids, blocks=plan,
                          prompts=sv.prompts(proj.slug))


def previous_shot_lines(db, block_id: int) -> list[str]:
    """这一段现有 llm 镜头，每镜一行「动作｜台词（说话人：内容；…）」，带修改意见重写时给模型参考。"""
    with db.session() as s:
        shots = s.scalars(select(Shot).where(Shot.scene_block_id == block_id,
                                             Shot.origin == "llm")
                          .order_by(Shot.order, Shot.id)).all()
        lines = []
        for sh in shots:
            said = "；".join(f"{d.get('speaker', '')}：{d.get('zh', '')}"
                            for d in (sh.dialogue or []))
            lines.append(f"{sh.action}｜{said or '无台词'}")
        return lines


# ------------------------------------------------------------ 指纹
# 分镜规则（storyboard.md 里的写法要求）的版本号：改了模板里的规则就把它加一。
# 不强制的运行只比较输入指纹（风格锚、角色表、场景、段落文本、镜头数、时长预算、上一镜动作），
# 规则版本不同只提示「规则已更新」、不自动重跑——否则每改一次模板，所有剧集下一次整集运行
# 都会删光镜头、选定的首帧/视频和手改的内容，重新跑一遍 GPU。
STORYBOARD_RULES_VERSION = 2
RULES_OUTDATED_HINT = "分镜规则已更新，要按新规则重出请强制重跑"

# 镜头上会被用户改动的字段：生成时记下每镜的哈希，之后对不上就是改过（含拆分出来的新镜头）
SHOT_CONTENT_FIELDS = ("shot_size", "camera_move", "action", "dialogue", "cast", "props",
                       "image_prompt", "motion_prompt", "audio_prompt", "duration",
                       "transition_out", "image_engine", "video_engine", "use_end_frame",
                       "continue_from_prev")


def storyboard_rules(prompts) -> str:
    """这次用的分镜规则：内置模板就是版本号；项目目录里有覆盖模板时再带上覆盖模板的内容哈希。"""
    source = getattr(prompts, "source", None)
    if source is None:
        return str(STORYBOARD_RULES_VERSION)
    src, overridden = source("storyboard")
    if not overridden:
        return str(STORYBOARD_RULES_VERSION)
    return f"{STORYBOARD_RULES_VERSION}+{input_hash('storyboard-template', src)[:8]}"


def storyboard_inputs_fp(anchor, characters, location, text, count, shot_seconds, max_seconds,
                         prev_action, block_seconds=None) -> str:
    """某段落分镜的输入指纹：只算数据，不算模板文字（模板改动由 STORYBOARD_RULES_VERSION 单独记）。
    修改意见、上一版镜头、前面段落已定的道具说法都不算：前两样靠 force 一定会跑，道具说法只是提示。"""
    return input_hash("storyboard-inputs", anchor=anchor or "", characters=characters,
                      location=location, text=text, count=int(count),
                      shot_seconds=float(shot_seconds), max_seconds=float(max_seconds),
                      prev_action=prev_action or "",
                      block_seconds=float(block_seconds) if block_seconds else None)


def shot_content_hash(sh) -> str:
    return input_hash("shot-content", **{f: getattr(sh, f, None) for f in SHOT_CONTENT_FIELDS})


@dataclass
class BlockCheck:
    unchanged: bool        # 输入没变（且这一段有 llm 镜头）：不强制时跳过
    rules_outdated: bool   # 现有镜头是按旧规则出的
    legacy: bool           # 旧格式指纹（整段渲染后提示词的哈希），没法重算，按没变处理


def judge_block(record, inputs_fp: str, rules: str, has_llm_shots: bool) -> BlockCheck:
    """按 stage_hashes 里这一段的记录判断要不要重跑。

    新格式：{"inputs": 输入指纹, "rules": 规则版本, "shots": {镜头 id: 内容哈希}}。
    旧格式是一个字符串（整段提示词的哈希，模板一改就全对不上）：有 llm 镜头时按没变处理，
    由调用方换成新格式，不重跑。"""
    if isinstance(record, dict):
        same = record.get("inputs") == inputs_fp and has_llm_shots
        return BlockCheck(same, has_llm_shots and record.get("rules") != rules, False)
    if isinstance(record, str) and record and has_llm_shots:
        return BlockCheck(True, True, True)
    return BlockCheck(False, False, False)


def upgraded_record(inputs_fp: str) -> dict:
    """旧格式升级成新格式：规则版本未知（算旧规则），镜头内容哈希未知（不知道哪些手改过）。"""
    return {"inputs": inputs_fp, "rules": None, "shots": None}


def edited_shot_ids(record, shots) -> list[int]:
    """这一段里生成后被改过的 llm 镜头（记录里没有的 id 也算，比如拆分出来的）；旧记录不知道，返回空。"""
    known = record.get("shots") if isinstance(record, dict) else None
    if not isinstance(known, dict):
        return []
    return [sh.id for sh in shots if known.get(str(sh.id)) != shot_content_hash(sh)]


def known_props(db, block_ids: list[int]) -> list[str]:
    """前面这些段落的镜头里已经定下的道具英文说法（按镜头顺序去重），给后面的段落沿用。"""
    if not block_ids:
        return []
    with db.session() as s:
        order = {bid: i for i, bid in enumerate(block_ids)}
        shots = s.scalars(select(Shot).where(Shot.scene_block_id.in_(block_ids))).all()
        shots = sorted(shots, key=lambda x: (order[x.scene_block_id], x.order, x.id))
        out: list[str] = []
        for sh in shots:
            for p in sh.props or []:
                p = p.strip() if isinstance(p, str) else ""
                if p and p not in out:
                    out.append(p)
        return out[:30]


def storyboard_prompt_fp(prompts, anchor, characters, location, text, count, shot_seconds,
                         max_seconds, prev_action, note: str | None = None,
                         previous_shots: list[str] | None = None,
                         block_seconds: float | None = None,
                         props: list[str] | None = None) -> tuple[str, str, str]:
    """某段落这次会用到的分镜提示词及其输入指纹（不调用 LLM）。

    note 是修改意见；previous_shots 是这一段上一版的镜头（只在有修改意见时提供）；
    props 是前面段落已定的道具英文说法。指纹见 storyboard_inputs_fp（不含这三样，也不含模板文字）。
    """
    system, user, _, _ = render_with_note(
        prompts, "storyboard", note, {"previous_shots": previous_shots or []},
        anchor=anchor, characters=characters, location=location, text=text,
        count=count, shot_seconds=shot_seconds, max_seconds=max_seconds, prev_action=prev_action,
        block_seconds=block_seconds, previous_shots=[], known_props=list(props or []))
    fp = storyboard_inputs_fp(anchor, characters, location, text, count, shot_seconds,
                              max_seconds, prev_action, block_seconds)
    return system, user, fp


@HANDLERS.register("storyboard")
def run_storyboard(ctx, payload: dict) -> dict:
    sv = ctx.services
    episode_id = int(payload["episode_id"])
    note = clean_note(payload.get("note"))
    force = bool(payload.get("force")) or note is not None  # 带修改意见就一定重跑
    manual = payload.get("manual") or {}
    only = set(payload.get("scene_block_ids") or [])
    with sv.db.session() as s:
        plan_data = build_storyboard_plan(s, sv, episode_id)
        shot_seconds, max_seconds = plan_data.shot_seconds, plan_data.max_seconds
        anchor, characters, char_ids = plan_data.anchor, plan_data.characters, plan_data.char_ids
        plan = plan_data.blocks
        prompts = plan_data.prompts
    rules = storyboard_rules(prompts)

    done = skipped = 0
    prev_action = ""
    for idx, blk in enumerate(plan):
        if only and blk["id"] not in only:
            prev_action = _last_action(sv.db, blk["id"]) or prev_action
            continue
        ctx.check_canceled()
        n = blk["count"]
        label = f"段落 {blk['order'] + 1}"
        previous = previous_shot_lines(sv.db, blk["id"]) if note else []
        props = known_props(sv.db, [b["id"] for b in plan[:idx]])
        system, user, fp = storyboard_prompt_fp(prompts, anchor, characters, blk["location"],
                                               blk["text"], n, shot_seconds, max_seconds,
                                               prev_action, note, previous,
                                               block_seconds=blk["seconds"], props=props)
        key = f"storyboard:{blk['id']}"
        with sv.db.session() as s:
            ep = s.get(Episode, episode_id)
            existing = s.scalar(select(func.count()).select_from(Shot)
                                .where(Shot.scene_block_id == blk["id"], Shot.origin == "llm"))
            record = (ep.stage_hashes or {}).get(key)
            judged = judge_block(record, fp, rules, existing > 0)
            if judged.legacy and not force:
                # 旧格式指纹没法按新算法重算：当作没变，换成新格式，不重跑（不删镜头和选定的首帧/视频）
                ep.stage_hashes = {**(ep.stage_hashes or {}), key: upgraded_record(fp)}
        if judged.unchanged and not force:
            if judged.legacy:
                ctx.log(f"{label}：旧版指纹，按输入没变处理，已换成新格式；{RULES_OUTDATED_HINT}")
            elif judged.rules_outdated:
                ctx.log(f"{label}：输入没变，跳过；{RULES_OUTDATED_HINT}")
            else:
                ctx.log(f"{label}：输入没变，跳过")
            skipped += 1
            prev_action = _last_action(sv.db, blk["id"]) or prev_action
            continue

        tries = [0]

        def check(out: StoryboardOut, n: int = n, target: float = blk["seconds"]) -> None:
            tries[0] += 1
            if len(out.shots) != n:
                raise ValueError(f"需要正好 {n} 个镜头，实际给了 {len(out.shots)} 个")
            unknown = {c for sh in out.shots for c in sh.cast} - set(char_ids)
            if unknown:
                raise ValueError(f"cast 里有未知角色 {sorted(unknown)}，只能用：{sorted(char_ids)}")
            cjk = cjk_problems(out.shots)
            if cjk:
                raise ValueError("；".join(cjk) + "。image_prompt、motion_prompt、audio_prompt 和 props "
                                 "必须全英文，不能有任何中文字符：角色名要换成角色表里该角色的英文外貌"
                                 "描述（如 a stocky man in his fifties）；画面里要出现的招牌、字条上的字"
                                 "写在英文双引号里（如 a sign reading \"老周修车\"）或改成英文描述")
            # 最后一次机会时不再因为「差得太多」打回，按比例缩放兜底（台词放不下的照样打回）
            _, err = budget_durations(out.shots, target, max_seconds,
                                      rewrite_over=tries[0] < 1 + MAX_RETRIES)
            if err:
                raise ValueError(err)

        out = sv.llm.generate("storyboard", system, user, StoryboardOut, check=check,
                              log=ctx.log, manual=manual, key=key)
        with sv.db.session() as s:
            gone = s.scalars(select(Shot.id).where(Shot.scene_block_id == blk["id"],
                                                   Shot.origin == "llm")).all()
            delete_slots(s, "shot", gone)
            s.execute(delete(Shot).where(Shot.scene_block_id == blk["id"], Shot.origin == "llm"))
            trimmed = 0
            durs, _ = budget_durations(out.shots, blk["seconds"], max_seconds, rewrite_over=False)
            added: list[Shot] = []
            for j, (sh, dur) in enumerate(zip(out.shots, durs)):
                dialogue = []
                for d in sh.dialogue:
                    if d.start >= dur - 0.2:
                        trimmed += 1
                        continue
                    dd = d.model_dump()
                    dd["end"] = min(dd["end"], dur)
                    dialogue.append(dd)
                row = Shot(
                    episode_id=episode_id, scene_block_id=blk["id"], order=blk["order"] * 1000 + j,
                    shot_size=sh.shot_size, camera_move=sh.camera_move, action=sh.action,
                    dialogue=dialogue,
                    cast=[{"character_id": char_ids[c], "variation_id": None} for c in sh.cast],
                    props=list(sh.props), image_prompt=sh.image_prompt,
                    motion_prompt=sh.motion_prompt, audio_prompt=sh.audio_prompt, duration=dur,
                    transition_out=sh.transition_out)
                s.add(row)
                added.append(row)
            s.flush()
            raw = [float(sh.duration) for sh in out.shots]
            if [min(d, max_seconds) for d in raw] != durs:
                ctx.log(f"{label}：镜头总时长 {sum(raw):.1f}s 和本段预算 "
                        f"{blk['seconds']:.1f}s 差太多，按比例调成 "
                        f"{'+'.join(f'{d:g}' for d in durs)} = {sum(durs):.1f}s")
            if trimmed:
                ctx.log(f"{label}：时长截断后裁掉 {trimmed} 句超出的台词")
            ep = s.get(Episode, episode_id)
            ep.stage_hashes = {**(ep.stage_hashes or {}), key: {
                "inputs": fp, "rules": rules,
                "shots": {str(x.id): shot_content_hash(x) for x in added}}}
        done += 1
        prev_action = out.shots[-1].action
        ctx.log(f"{label}：{n} 个镜头")

    with sv.db.session() as s:
        ep = s.get(Episode, episode_id)
        if done:
            if not only:
                orphan = (Shot.episode_id == episode_id, Shot.scene_block_id.is_(None),
                         Shot.origin == "llm")
                delete_slots(s, "shot", s.scalars(select(Shot.id).where(*orphan)).all())
                s.execute(delete(Shot).where(*orphan))
            block_order = {b.id: b.order for b in ep.scene_blocks}
            shots = list(s.scalars(select(Shot).where(Shot.episode_id == episode_id)))
            shots.sort(key=lambda x: (block_order.get(x.scene_block_id, 10**6), x.order, x.id))
            renumber_shots(s, episode_id, [x.id for x in shots])
        ep.status = "storyboarded"
    continue_chain(ctx, episode_id, payload)
    return {"blocks_done": done, "blocks_skipped": skipped}
