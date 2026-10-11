"""分镜阶段：按目标时长和引擎甜区算镜头预算，按剧本段落分配，逐段让 LLM 出镜头。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from backend.domain.archive import archive_and_delete_slots
from backend.domain.backup import BACKUP_KEEP, backup_database
from backend.domain.hashing import input_hash
from backend.domain.models import Episode, Shot
from backend.domain.shots import renumber_shots
from backend.domain.style_presets import prompt_vars
from backend.llm.schemas import StoryboardOut
from backend.pipeline.prompting import char_token, short_ref
from backend.pipeline.common import (clean_note, continue_chain, load_episode, render_with_note,
                                     template_rules)
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


def cjk_outside_quotes(text: str, names=()) -> str:
    """引号外的中文；引号里的中文当作画面上要出现的字放行，但引号里写角色名不行（那是想偷渡名字）。"""
    text = text or ""
    names = [n for n in names if n]
    quoted_names = "".join(n for q in _QUOTED.findall(text) for n in names if n in q)
    return quoted_names + cjk_chars(_QUOTED.sub(" ", text))


def cjk_problems(shots, names=()) -> list[str]:
    out = []
    for i, sh in enumerate(shots, start=1):
        for field in ("image_prompt", "motion_prompt", "audio_prompt"):
            bad = cjk_outside_quotes(getattr(sh, field, "") or "", names)
            if bad:
                out.append(f"第 {i} 镜 {field} 里有中文「{bad[:20]}」")
        bad = "".join(cjk_outside_quotes(p, names) for p in (getattr(sh, "props", None) or []))
        if bad:
            out.append(f"第 {i} 镜 props 里有中文「{bad[:20]}」")
    return out


# ------------------------------------------------------------ 角色名拼音
# CJK 校验拦不住拼音名（Lao Zhou rolls the scooter…），拼音名进了视频提示词，模型不知道是谁，
# 还会和「the stocky man」这种外貌简称并存。按角色表把名字转成拼音，几种写法都查。
PROMPT_FIELDS = ("image_prompt", "motion_prompt", "audio_prompt")


def pinyin_variants(name: str) -> list[list[str]]:
    """名字的拼音音节组合：全名（老周 → lao zhou），三个字及以上再加去掉姓的名（林小满 → xiao man）。
    单音节（一个字、英文名）不查：太容易误伤正常英文单词。"""
    from pypinyin import lazy_pinyin

    syl = [x.lower() for x in lazy_pinyin(name or "") if x.isascii() and x.isalpha()]
    if len(syl) != len(name or "") or len(syl) < 2:
        return []
    out = [syl]
    if len(syl) >= 3:
        out.append(syl[1:])
    return out


def pinyin_pattern(name: str) -> re.Pattern | None:
    """不分大小写，音节之间可以有空格、连字符或撇号，也可以连写（Lao Zhou / Laozhou / lao-zhou）。"""
    alts = [r"[\s\-']?".join(re.escape(s) for s in v) for v in pinyin_variants(name)]
    if not alts:
        return None
    return re.compile(r"(?<![A-Za-z])(?:" + "|".join(alts) + r")(?![A-Za-z])", re.I)


# 连写起来正好是常见英文单词的拼音（常娥 → change、图图 → tutu、安娜 → anna……）：
# 不当成角色名拼音，不打回、不替换，只在日志里提一句
ENGLISH_WORDS = frozenset((
    "change chan dian hang hen hang ban bang can cane den dime dine dong fan fang gang hen lane lean "
    "line long main man mane many men mine moan mole nan nine pan pang pen pine ping pong ran rang "
    "rein ring sane san sang shame shine shoe sue tan tang ten tune tutu wan wane wen wine wing yen "
    "zen anna bean bin bone cuba gene hue lingo mango mean mono nina pain panda pena rain renee "
    "sangria shin shine shone sine tango tine tone wane").split())


def _is_english(written: str) -> bool:
    """连写成一个词、而且是常见英文单词（分开写的「Chang E」还是拼音名）。"""
    return written.isalpha() and written.lower() in ENGLISH_WORDS


def pinyin_hits(text: str, names, *, english: list | None = None) -> list[tuple[str, str]]:
    """[(角色名, 原文里写的拼音)]。连写起来是常见英文单词的不算（english 里记下来）。"""
    out = []
    for name in names:
        pat = pinyin_pattern(name)
        if pat is None:
            continue
        for m in pat.finditer(text or ""):
            if _is_english(m.group(0)):
                if english is not None:
                    english.append((name, m.group(0)))
                continue
            out.append((name, m.group(0)))
    return out


def pinyin_problems(shots, names) -> list[str]:
    out = []
    for i, sh in enumerate(shots, start=1):
        fields = [(f, getattr(sh, f, "") or "") for f in PROMPT_FIELDS]
        fields += [("props", " | ".join(getattr(sh, "props", None) or []))]
        for field, text in fields:
            for name, written in pinyin_hits(text, names):
                out.append(f"第 {i} 镜 {field} 里写了角色名的拼音 {written}（{name}），"
                           "换成英文外貌简称（如 the stocky man）")
    return out


def replace_pinyin(shots, refs: dict[str, str]) -> int:
    """最后一次机会还写了拼音名：直接换成英文外貌简称（不为这个让整段失败）。返回替换次数。"""
    n = 0
    for sh in shots:
        for field in PROMPT_FIELDS:
            text = getattr(sh, field, "") or ""
            for name, ref in refs.items():
                pat = pinyin_pattern(name)
                if pat is not None:
                    def sub(m: re.Match, ref: str = ref) -> str:
                        return m.group(0) if _is_english(m.group(0)) else ref
                    new = pat.sub(sub, text)
                    n += sum(1 for m in pat.finditer(text) if not _is_english(m.group(0)))
                    text = new
            setattr(sh, field, text)
    return n


# ------------------------------------------------------------ 外貌占位符、说话人、语速
MAX_CHARS_PER_SECOND = 5.5
_PUNCT = re.compile(r"[\s，。、；：？！…—「」『』“”‘’（）《》,.;:?!'\"()\-~～·]")


COPIED_NGRAM = 8


def _copied(text: str, look: str, n: int = COPIED_NGRAM) -> str | None:
    """text 里有没有和 look 连续 n 个词一样的片段（不分大小写），有就返回那一段。"""
    tw = re.findall(r"[a-z0-9][a-z0-9'-]*", (text or "").lower())
    lw = re.findall(r"[a-z0-9][a-z0-9'-]*", (look or "").lower())
    grams = {tuple(tw[i:i + n]) for i in range(len(tw) - n + 1)}
    for i in range(len(lw) - n + 1):
        if tuple(lw[i:i + n]) in grams:
            return " ".join(lw[i:i + n])
    return None


def token_problems(shots, char_ids: dict[str, int], looks: dict[str, str] | None = None) -> list[str]:
    """image_prompt 里的外貌占位符：只能指向角色表里的人、cast 里的每个人都要有、不在 cast 里的不要写；
    motion_prompt / audio_prompt / props 里不能有占位符（视频提示词不会替换它们）。
    looks（角色名 → 外貌）给了时，image_prompt 里又抄了外貌连续 8 个词以上也报错：出图时占位符会再填一遍。"""
    from backend.pipeline.prompting import CHAR_TOKEN, char_token

    known = {cid: name for name, cid in char_ids.items()}
    out = []
    for i, sh in enumerate(shots, start=1):
        for name in sh.cast:
            hit = _copied(sh.image_prompt or "", (looks or {}).get(name, ""))
            if hit:
                out.append(f"第 {i} 镜 image_prompt 里抄了 {name} 的外貌（「{hit}」…），外貌由占位符"
                           f"{char_token(char_ids[name]) if name in char_ids else ''}出图时自动填，"
                           "你只写简短指代和动作")
        ids = {int(x) for x in CHAR_TOKEN.findall(sh.image_prompt or "")}
        bad = sorted(ids - set(known))
        if bad:
            out.append(f"第 {i} 镜 image_prompt 里的占位符 {', '.join(char_token(x) for x in bad)} "
                       "不在角色表里")
        cast_ids = {char_ids[c] for c in sh.cast if c in char_ids}
        missing = [known[c] for c in sorted(cast_ids - ids)]
        if missing:
            out.append(f"第 {i} 镜 cast 里有 {'、'.join(missing)}，image_prompt 里要写他的外貌占位符"
                       f"（{'、'.join(char_token(char_ids[n]) for n in missing)}），单独成句，"
                       "放在描写他的句子前面")
        extra = [known[c] for c in sorted((ids & set(known)) - cast_ids)]
        if extra:
            out.append(f"第 {i} 镜 image_prompt 里写了 {'、'.join(extra)} 的占位符，但 cast 里没有他；"
                       "看得见就加进 cast，看不见就删掉占位符")
        for field in ("motion_prompt", "audio_prompt"):
            if CHAR_TOKEN.search(getattr(sh, field, "") or ""):
                out.append(f"第 {i} 镜 {field} 里不能写外貌占位符，用英文外貌简称（如 the stocky man）")
        if any(CHAR_TOKEN.search(p) for p in sh.props or []):
            out.append(f"第 {i} 镜 props 里不能写外貌占位符")
    return out


def speaker_problems(shots, names) -> list[str]:
    known = set(names)
    out = []
    for i, sh in enumerate(shots, start=1):
        for d in sh.dialogue:
            if d.speaker not in known:
                out.append(f"第 {i} 镜台词「{d.zh}」的说话人 {d.speaker} 不在角色表里；说话人只能用角色表里的"
                           f"名字（{'、'.join(sorted(known)) or '本集没有角色'}），路人要说话先在剧本角色里补上")
    return out


def speech_rate_problems(shots) -> list[str]:
    out = []
    for i, sh in enumerate(shots, start=1):
        for d in sh.dialogue:
            chars = len(_PUNCT.sub("", d.zh))
            span = float(d.end) - float(d.start)
            if span > 0 and chars / span > MAX_CHARS_PER_SECOND:
                out.append(f"第 {i} 镜台词「{d.zh}」在 {span:.1f} 秒里说 {chars} 个字太快"
                           f"（每秒不超过 {MAX_CHARS_PER_SECOND:g} 个字），放宽 start/end 或删字")
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
    aspect: str = "9:16"
    # 发给模板的角色表：比 characters 多了 id 和外貌占位符 token（characters 保持老样子，算老记录的指纹）
    char_table: list[dict] = field(default_factory=list)
    # 项目的风格预设：只进提示词，不进指纹（换预设不自动重写分镜）
    preset: str = "live_action"


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
    char_table = [{**ch, "id": c.id, "token": char_token(c.id)}
                  for ch, c in zip(characters, proj.characters)]
    locs = {l.id: {"name": l.name, "time_of_day": l.time_of_day, "atmosphere": l.atmosphere,
                   "visual_prompt": l.visual_prompt} for l in proj.locations}
    plan = [{"id": b.id, "order": b.order, "text": b.text, "location": locs.get(b.location_id),
             "count": n, "seconds": block_seconds(ep.target_seconds, n, total_count)}
            for b, n in zip(blocks, counts)]
    return StoryboardPlan(ep=ep, shot_seconds=shot_seconds, max_seconds=max_seconds, anchor=anchor,
                          characters=characters, char_ids=char_ids, blocks=plan,
                          prompts=sv.prompts(proj.slug), aspect=proj.aspect or "9:16",
                          char_table=char_table, preset=proj.style_preset or "live_action")


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
#   v3：image_prompt 里用外貌占位符 {{char:<id>}}（出图时按景别填当前外貌）、不写拼音名、
#       说话人和语速校验、规则 6 不再和风格锚重叠、低风险的镜头运动词表
#   v4：风格预设（分镜里的风格说法；商业广告要产品特写插入镜头和结尾行动号召镜头）
#   v5：audio_prompt 只写画面里的声音、不写音乐，以 No background music. 结尾（音乐交给成片配乐）；
#       结尾要人物留在画面里的镜头不写成长静止镜头
#   v6：AI 短剧（不再叫漫剧）；两人同框时正文 60–90 词；占位符后一句用「The + 简短指代」开头、
#       全镜逐字一致（视频对白按它认说话人）；只露手 / 腰的插入镜头只写部位、系统只填服装
STORYBOARD_RULES_VERSION = 6
RULES_OUTDATED_HINT = "分镜规则已更新，要按新规则重出请强制重跑"

# 镜头上会被用户改动的字段：生成时记下每镜的哈希，之后对不上就是改过（含拆分出来的新镜头）
SHOT_CONTENT_FIELDS = ("shot_size", "camera_move", "action", "dialogue", "cast", "props",
                       "image_prompt", "motion_prompt", "audio_prompt", "duration",
                       "transition_out", "image_engine", "video_engine", "use_end_frame",
                       "continue_from_prev")


def storyboard_rules(prompts) -> str:
    """这次用的分镜规则：内置模板就是版本号；项目目录里有覆盖模板时再带上覆盖模板的内容哈希。"""
    return template_rules(prompts, "storyboard", STORYBOARD_RULES_VERSION)


def storyboard_inputs_fp(anchor, characters, location, text, count, shot_seconds, max_seconds,
                         prev_action, block_seconds=None, aspect: str = "9:16") -> str:
    """某段落分镜的输入指纹：只算数据，不算模板文字（模板改动由 STORYBOARD_RULES_VERSION 单独记）。
    修改意见、上一版镜头、前面段落已定的道具说法都不算：前两样靠 force 一定会跑，道具说法只是提示。"""
    return input_hash("storyboard-inputs", anchor=anchor or "", characters=characters,
                      location=location, text=text, count=int(count),
                      shot_seconds=float(shot_seconds), max_seconds=float(max_seconds),
                      prev_action=prev_action or "",
                      block_seconds=float(block_seconds) if block_seconds else None,
                      aspect=aspect or "9:16")


def placeholder_characters(characters: list[dict]) -> list[dict]:
    """用外貌占位符出的分镜，角色外貌不进指纹：换造型时镜头靠占位符自动跟上，不用重写分镜
    （以前外貌抄进每镜 image_prompt，外貌一改，整集分镜要么不跟、要么全删重出）。"""
    return [{"name": c.get("name", "")} for c in characters]


def block_fps(plan: "StoryboardPlan", blk: dict, prev_action: str) -> tuple[str, str]:
    """(新格式指纹, 老格式指纹)：新格式（用占位符的分镜）的角色表只有名字，老格式带外貌。"""
    args = (blk["location"], blk["text"], blk["count"], plan.shot_seconds, plan.max_seconds,
            prev_action, blk["seconds"], plan.aspect)
    return (storyboard_inputs_fp(plan.anchor, placeholder_characters(plan.characters), *args),
            storyboard_inputs_fp(plan.anchor, plan.characters, *args))


def pick_fp(record, fp_new: str, fp_old: str) -> str:
    """按记录当时的格式选指纹比：用占位符出的（记录里 placeholders=True）比新格式，其余比老格式。"""
    return fp_new if isinstance(record, dict) and record.get("placeholders") else fp_old


def rebase_after_look_change(s: Session, sv, char, old_looks: list[str],
                             changed: dict[int, str]) -> int:
    """「把镜头里的外貌更新为当前造型」之后，v2（外貌抄进 image_prompt）出的段落记录跟着更新：
    这一段里这个角色出场的 llm 镜头全都换干净了（或本来就用占位符），而且记录的指纹正好是
    「换造型之前的外貌 + 现在的其他输入」——就把指纹改成现在的外貌，换过的镜头内容哈希也更新
    （系统替换的，不算用户改过）。否则下一次不强制的整集运行会要求删光这一段的镜头重出。
    返回更新了几段。"""
    from backend.pipeline.prompting import char_token

    token = char_token(char.id)
    n = 0
    for ep in s.scalars(select(Episode).where(Episode.project_id == char.project_id)).all():
        try:
            plan = build_storyboard_plan(s, sv, ep.id)
        except JobError:
            continue
        hashes = dict(ep.stage_hashes or {})
        dirty = False
        prev_action = ""
        for blk in plan.blocks:
            key = f"storyboard:{blk['id']}"
            rec = hashes.get(key)
            shots = list(s.scalars(select(Shot).where(Shot.scene_block_id == blk["id"],
                                                      Shot.origin == "llm")))
            mine = [sh for sh in shots
                    if token in (sh.image_prompt or "")
                    or any(isinstance(c, dict) and c.get("character_id") == char.id
                           for c in sh.cast or [])]
            if isinstance(rec, dict) and not rec.get("placeholders") and mine \
                    and all(token in (sh.image_prompt or "") for sh in mine):
                _, cur = block_fps(plan, blk, prev_action)
                args = (blk["location"], blk["text"], blk["count"], plan.shot_seconds,
                        plan.max_seconds, prev_action, blk["seconds"], plan.aspect)
                for old in old_looks:
                    chars = [{**c, "visual_prompt": old} if c.get("name") == char.name else c
                             for c in plan.characters]
                    if rec.get("inputs") != cur and \
                            storyboard_inputs_fp(plan.anchor, chars, *args) == rec.get("inputs"):
                        known = rec.get("shots")
                        if isinstance(known, dict):
                            known = dict(known)
                            for sh in shots:
                                if sh.id in changed and known.get(str(sh.id)) == changed[sh.id]:
                                    known[str(sh.id)] = shot_content_hash(sh)
                        hashes[key] = {**rec, "inputs": cur, "shots": known}
                        dirty = True
                        n += 1
                        break
            last = s.scalar(select(Shot).where(Shot.scene_block_id == blk["id"])
                            .order_by(Shot.order.desc(), Shot.id.desc()))
            prev_action = (last.action if last else "") or prev_action
        if dirty:
            ep.stage_hashes = hashes
    s.flush()
    return n


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
                         props: list[str] | None = None,
                         aspect: str = "9:16", preset: str | None = None) -> tuple[str, str, str]:
    """某段落这次会用到的分镜提示词及其输入指纹（不调用 LLM）。

    note 是修改意见；previous_shots 是这一段上一版的镜头（只在有修改意见时提供）；
    props 是前面段落已定的道具英文说法。指纹见 storyboard_inputs_fp（不含这三样，也不含模板文字）。
    """
    system, user, _, _ = render_with_note(
        prompts, "storyboard", note, {"previous_shots": previous_shots or []},
        anchor=anchor, characters=characters, location=location, text=text,
        count=count, shot_seconds=shot_seconds, max_seconds=max_seconds, prev_action=prev_action,
        block_seconds=block_seconds, previous_shots=[], known_props=list(props or []),
        aspect=aspect, **prompt_vars(preset))
    legacy = [{"name": c.get("name", ""), "visual_prompt": c.get("visual_prompt", "")}
              for c in characters]
    fp = storyboard_inputs_fp(anchor, legacy, location, text, count, shot_seconds,
                              max_seconds, prev_action, block_seconds, aspect)
    return system, user, fp


class ShotDiscarder:
    """分镜重出删旧镜头的唯一入口：真要删东西时先自动备份数据库（一次任务只备份一次），
    再把旧镜头的候选挪进「历史候选」，最后删槽位（镜头行由调用方删）。"""

    def __init__(self, ctx, reason: str = "storyboard"):
        self.ctx, self.reason, self.backed_up = ctx, reason, False

    def __call__(self, s: Session, shot_ids) -> None:
        ids = list(shot_ids)
        if not ids:
            return
        if not self.backed_up:
            self.backed_up = True
            path = backup_database(self.ctx.services.db, self.reason)
            if path is not None:
                self.ctx.log(f"已自动备份数据库：{path.name}（保留最近 {BACKUP_KEEP} 份）")
            else:
                self.ctx.log("自动备份数据库失败（详见服务日志），继续执行")
        n = archive_and_delete_slots(s, ids, self.reason)
        if n:
            self.ctx.log(f"删掉的 {len(ids)} 个旧镜头有 {n} 个首帧/视频候选，已放进「历史候选」，"
                         "可以在镜头详情里恢复")


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
    discard = ShotDiscarder(ctx)

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
        system, user, _ = storyboard_prompt_fp(prompts, anchor, plan_data.char_table,
                                               blk["location"],
                                               blk["text"], n, shot_seconds, max_seconds,
                                               prev_action, note, previous,
                                               block_seconds=blk["seconds"], props=props,
                                               aspect=plan_data.aspect, preset=plan_data.preset)
        # 模板里写了占位符的说明才按占位符校验（项目目录里的老覆盖模板不知道占位符）
        placeholders = "{{char:" in system
        fp_new, fp_old = block_fps(plan_data, blk, prev_action)
        key = f"storyboard:{blk['id']}"
        with sv.db.session() as s:
            ep = s.get(Episode, episode_id)
            existing = s.scalar(select(func.count()).select_from(Shot)
                                .where(Shot.scene_block_id == blk["id"], Shot.origin == "llm"))
            record = (ep.stage_hashes or {}).get(key)
            judged = judge_block(record, pick_fp(record, fp_new, fp_old), rules, existing > 0)
            if judged.legacy and not force:
                # 旧格式指纹没法按新算法重算：当作没变，换成新格式，不重跑（不删镜头和选定的首帧/视频）
                ep.stage_hashes = {**(ep.stage_hashes or {}), key: upgraded_record(fp_old)}
        if judged.unchanged and not force:
            if judged.legacy:
                ctx.log(f"{label}：旧版记录，无法判断有没有变，先不动（已换成新格式）；"
                        "要重出请用重新生成")
            elif judged.rules_outdated:
                ctx.log(f"{label}：输入没变，跳过；{RULES_OUTDATED_HINT}")
            else:
                ctx.log(f"{label}：输入没变，跳过")
            skipped += 1
            prev_action = _last_action(sv.db, blk["id"]) or prev_action
            continue

        short_refs = {c["name"]: short_ref(c.get("visual_prompt")) for c in characters}

        def check(out: StoryboardOut, n: int = n, target: float = blk["seconds"], *,
                  last: bool = True) -> None:
            if len(out.shots) != n:
                raise ValueError(f"需要正好 {n} 个镜头，实际给了 {len(out.shots)} 个")
            unknown = {c for sh in out.shots for c in sh.cast} - set(char_ids)
            if unknown:
                raise ValueError(f"cast 里有未知角色 {sorted(unknown)}，只能用：{sorted(char_ids)}")
            cjk = cjk_problems(out.shots, list(char_ids))
            if cjk:
                raise ValueError("；".join(cjk) + "。image_prompt、motion_prompt、audio_prompt 和 props "
                                 "必须全英文，不能有任何中文字符：人物在 image_prompt 里第一次出现写他的"
                                 "外貌占位符，其他地方用英文外貌简称（如 the stocky man）；画面里要出现的"
                                 "招牌、字条上的字写在英文双引号里（如 a sign reading \"老周修车\"）或改成"
                                 "英文描述")
            problems = speaker_problems(out.shots, list(char_ids))
            if placeholders:
                problems += token_problems(out.shots, char_ids,
                                           {c["name"]: c.get("visual_prompt") or "" for c in characters})
            if not last:
                problems += pinyin_problems(out.shots, list(char_ids))
                problems += speech_rate_problems(out.shots)
            elif replace_pinyin(out.shots, short_refs):
                # 最后一次机会：拼音名直接换成英文外貌简称，不为这个让整段失败
                ctx.log(f"{label}：镜头提示词里还有角色名拼音，已换成英文外貌简称")
            english: list = []
            for sh in out.shots:
                for f in PROMPT_FIELDS:
                    pinyin_hits(getattr(sh, f, "") or "", list(char_ids), english=english)
            if english:
                ctx.log(f"{label}：提示词里的 {'、'.join(sorted({w for _, w in english}))} 和角色名拼音"
                        "同形，按英文单词处理，没有改")
            if problems:
                raise ValueError("；".join(problems))
            # 最后一次机会（网关按真实调用次数告诉我们；手工粘贴的输出也算）不再因为「差得太多」打回，
            # 按比例缩放兜底（台词放不下的照样打回）
            _, err = budget_durations(out.shots, target, max_seconds, rewrite_over=not last)
            if err:
                raise ValueError(err)

        out = sv.llm.generate("storyboard", system, user, StoryboardOut, check=check,
                              log=ctx.log, manual=manual, key=key)
        with sv.db.session() as s:
            gone = s.scalars(select(Shot.id).where(Shot.scene_block_id == blk["id"],
                                                   Shot.origin == "llm")).all()
            discard(s, gone)
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
                "inputs": fp_new if placeholders else fp_old, "rules": rules,
                "placeholders": placeholders,
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
                discard(s, s.scalars(select(Shot.id).where(*orphan)).all())
                s.execute(delete(Shot).where(*orphan))
            block_order = {b.id: b.order for b in ep.scene_blocks}
            shots = list(s.scalars(select(Shot).where(Shot.episode_id == episode_id)))
            shots.sort(key=lambda x: (block_order.get(x.scene_block_id, 10**6), x.order, x.id))
            renumber_shots(s, episode_id, [x.id for x in shots])
        ep.status = "storyboarded"
    continue_chain(ctx, episode_id, payload)
    return {"blocks_done": done, "blocks_skipped": skipped}
