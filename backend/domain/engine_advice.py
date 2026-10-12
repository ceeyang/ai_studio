"""按镜头推荐视频引擎（省钱：简单镜头用便宜的，难镜头用强的）。只给建议，不自动换。

纯函数：输入一个镜头（景别、运镜、动作、出场角色、台词、道具、时长、接上一镜、尾帧、生成方式）和已注册的视频引擎
（caps、runtime、能不能用），输出：

- 难度 easy / medium / hard、分数、中文原因（多人同框、大动作、复杂运镜、台词要对口型…）；
- 硬性要求：有台词要有声引擎、用尾帧要支持尾帧、参考图直出要支持 ref_mode、时长不超过引擎上限；
- 推荐：只从能用（ready）且满足硬性要求的引擎里挑。简单镜头先便宜的（本机 ComfyUI → 网页额度 → 云端 → API），
  难镜头先强模型（同档里再挑便宜的）；
- 当前引擎（镜头上选的，没选就是项目的）满不满足硬性要求，不满足时列出问题；
- 要不要建议换：当前引擎不满足要求 / 用不了，或难镜头用的是标准档而有强模型可用，或简单镜头用的是按量付费的
  强模型而有更便宜的可用。

档位表 ENGINE_TIER、花费排序 RUNTIME_COST 都是数据，按实际体验改这两张表就行。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal

from backend.domain.beats import waste_seconds
from backend.domain.shots import refs_mode

Difficulty = Literal["easy", "medium", "hard"]
Tier = Literal["strong", "standard"]

# 引擎档位：strong = 大动作、多人同框更稳的强模型；standard = 简单镜头够用。没写的（如 RunningHub 自定义工作流）按 standard。
# 依据（2026-10）：Seedance 2.x、可灵 v3、Veo 3.x、Sora 2 在多人、打斗、复杂运镜上明显更稳；H3 / Wan 2.2 本机跑，
# 动作幅度大时容易糊脸、肢体变形；Gemini / Muse 网页额度有限、时长固定或只能写进提示词。
ENGINE_TIER: dict[str, Tier] = {
    "seedance_video": "strong",
    "kling_video": "strong",
    "veo_video": "strong",
    "sora_video": "strong",
    "jimeng_video": "strong",
    "kling_web_video": "strong",
    "h3_video": "standard",
    "wan22_i2v": "standard",
    "gemini_video": "standard",
    "muse_video": "standard",
}

# 花费从低到高：本机显卡（不花钱）→ 网页（会员 / 免费额度）→ RunningHub 云端 → 官方 API（按量付费）
RUNTIME_COST: dict[str, int] = {"comfyui": 0, "browser": 1, "runninghub": 2, "api": 3}
RUNTIME_TEXT: dict[str, str] = {"comfyui": "本机显卡出，不花钱", "browser": "用网页会员 / 免费额度",
                                "runninghub": "RunningHub 云端，按次计费", "api": "官方 API，按量付费"}

# 大动作关键词（动作描述、运动提示词里找；中英文都认，英文按词边界）
BIG_ACTION_ZH = ("打斗", "打架", "格斗", "搏斗", "厮打", "扭打", "过招", "出拳", "挥拳", "踢", "砍",
                 "奔跑", "狂奔", "追逐", "追赶", "逃跑", "飞奔", "冲刺", "跳舞", "舞蹈", "起舞", "翻滚", "跳跃",
                 "跳起", "摔倒", "摔", "飞扑", "飞身", "爆炸", "坠落", "翻身", "骑马", "开枪", "射击", "厮杀")
BIG_ACTION_EN = ("fight", "fighting", "punch", "punches", "kick", "kicks", "brawl", "sword", "slash", "run",
                 "runs", "running", "chase", "chasing", "sprint", "sprints", "dance", "dancing", "dances",
                 "jump", "jumps", "jumping", "leap", "leaps", "tumble", "flip", "explosion", "explodes",
                 "falls", "falling", "gunfire", "shoots", "wrestle", "struggle", "struggles")
_EN_ACTION = re.compile(r"\b(" + "|".join(BIG_ACTION_EN) + r")\b", re.IGNORECASE)

# 固定机位（不算运镜）；其余都算运镜，复杂的再加分
STATIC_MOVES = {"", "固定", "固定镜头", "固定机位", "静止", "不动", "static", "fixed", "locked", "locked-off",
                "locked off", "still", "none", "无"}
COMPLEX_MOVE_ZH = ("环绕", "旋转", "360", "跟拍", "跟随", "航拍", "升降", "摇臂", "甩", "希区柯克", "变焦推拉",
                   "手持", "长镜头", "穿梭")
COMPLEX_MOVE_EN = ("orbit", "360", "tracking", "follow", "aerial", "drone", "crane", "whip",
                   "dolly zoom", "handheld", "steadicam", "fpv", "spiral", "rotate")

EASY_MAX = 1      # 分数 ≤ 1：简单
MEDIUM_MAX = 3    # 2–3：中等；≥ 4：难
TOP_N = 3         # 推荐列前几个


@dataclass(frozen=True)
class EngineInfo:
    """一个已注册的视频引擎：名字、显示名、运行方式、能力、现在能不能用（不能用时 note 说原因）。"""
    name: str
    label: str
    runtime: str
    caps: Any                 # VideoCaps（鸭子类型：has_audio / max_seconds / supports_end_frame / ref_mode）
    ready: bool = True
    note: str | None = None

    @property
    def tier(self) -> Tier:
        return ENGINE_TIER.get(self.name, "standard")

    @property
    def cost(self) -> int:
        return RUNTIME_COST.get(self.runtime, 3)


@dataclass
class EngineCheck:
    engine: str
    label: str
    tier: Tier
    runtime: str
    ready: bool
    note: str | None
    ok: bool                       # 满足这一镜的硬性要求
    problems: list[str] = field(default_factory=list)


@dataclass
class Recommendation:
    engine: str
    label: str
    why: str


@dataclass
class ShotAdvice:
    shot_id: int
    code: str
    difficulty: Difficulty
    score: int
    reasons: list[str]
    requirements: list[str]
    current_engine: str | None
    current_ok: bool
    current_problems: list[str]
    recommended: list[Recommendation]
    suggest_switch: bool
    switch_reason: str | None
    checks: list[EngineCheck]


@dataclass
class EpisodeAdvice:
    episode_id: int
    shots: list[ShotAdvice]
    switch_count: int


def _text(*parts: str | None) -> str:
    return " ".join(p for p in parts if p)


def big_actions(text: str) -> list[str]:
    """动作 / 运动描述里的大动作关键词（去重，保留出现顺序）。"""
    hits: list[str] = [w for w in BIG_ACTION_ZH if w in text]
    hits += [m.group(1).lower() for m in _EN_ACTION.finditer(text)]
    out: list[str] = []
    for h in hits:
        # 「狂奔」命中后「奔跑」之类的子词不再重复列；英文同词只列一次
        if h not in out and not any(h in o for o in out):
            out.append(h)
    return out


def camera_level(move: str) -> int:
    """运镜难度：0 固定；1 普通运镜（推、拉、摇、移）；2 复杂运镜（环绕、跟拍、航拍、甩镜…）。"""
    m = (move or "").strip().lower()
    if m in STATIC_MOVES:
        return 0
    if any(k in m for k in COMPLEX_MOVE_ZH) or any(k in m for k in COMPLEX_MOVE_EN):
        return 2
    return 1


def shot_difficulty(shot) -> tuple[int, list[str]]:
    """难度分数和原因（只算难度，不含硬性要求）。"""
    score = 0
    reasons: list[str] = []
    n_cast = len(getattr(shot, "cast", None) or [])
    if n_cast >= 3:
        score += 3
        reasons.append(f"多人同框（{n_cast} 人），容易串脸、动作对不上")
    elif n_cast == 2:
        score += 2
        reasons.append("两人同框，容易串脸")
    acts = big_actions(_text(getattr(shot, "action", ""), getattr(shot, "motion_prompt", "")))
    if acts:
        score += 2
        reasons.append(f"大动作（{'、'.join(acts[:4])}），容易糊、肢体变形")
    lvl = camera_level(getattr(shot, "camera_move", ""))
    if lvl:
        score += lvl
        move = (getattr(shot, "camera_move", "") or "").strip()
        reasons.append(f"复杂运镜（{move}）" if lvl == 2 else f"有运镜（{move}）")
    if getattr(shot, "dialogue", None):
        score += 1
        reasons.append("有台词，要出声、对口型")
    n_props = len(getattr(shot, "props", None) or [])
    if n_props >= 2:
        score += 1
        reasons.append(f"道具多（{n_props} 件），要拿稳、不变形")
    dur = float(getattr(shot, "duration", 0) or 0)
    if dur > 8:
        score += 1
        reasons.append(f"时长 {dur:g} 秒，长镜头越往后越容易走样")
    return score, reasons


def difficulty_of(score: int) -> Difficulty:
    if score <= EASY_MAX:
        return "easy"
    return "medium" if score <= MEDIUM_MAX else "hard"


def shot_requirements(shot) -> list[str]:
    """这一镜对视频引擎的硬性要求（界面上列出来）。"""
    out: list[str] = []
    if getattr(shot, "dialogue", None):
        out.append("有台词：要能出声的引擎")
    if getattr(shot, "use_end_frame", False):
        out.append("用尾帧：要支持尾帧的引擎")
    if refs_mode(shot):
        out.append("参考图直出：要支持参考图直出的引擎")
    dur = float(getattr(shot, "duration", 0) or 0)
    if dur > 0:
        out.append(f"时长 {dur:g} 秒：引擎最长不能短于这个")
    return out


def check_engine(shot, e: EngineInfo) -> EngineCheck:
    """引擎满不满足这一镜的硬性要求（不看能不能用，能不能用另记在 ready / note）。"""
    caps = e.caps
    problems: list[str] = []
    if getattr(shot, "dialogue", None) and not getattr(caps, "has_audio", False):
        problems.append("没有声音，台词出不来、对不上口型")
    if getattr(shot, "use_end_frame", False) and not getattr(caps, "supports_end_frame", False):
        problems.append("不支持尾帧，尾帧会被丢掉")
    if refs_mode(shot) and not getattr(caps, "ref_mode", False):
        problems.append("不支持参考图直出，出视频时会被拒绝")
    dur = float(getattr(shot, "duration", 0) or 0)
    top = float(getattr(caps, "max_seconds", 0) or 0)
    if top and dur > top + 1e-6:
        problems.append(f"最长 {top:g} 秒，这一镜 {dur:g} 秒会被截短")
    return EngineCheck(engine=e.name, label=e.label, tier=e.tier, runtime=e.runtime, ready=e.ready,
                       note=e.note, ok=not problems, problems=problems)


def _rank_key(difficulty: Difficulty, e: EngineInfo):
    strong_first = 0 if e.tier == "strong" else 1
    standard_first = 1 - strong_first
    if difficulty == "hard":
        return (strong_first, e.cost, e.name)
    if difficulty == "medium":
        return (e.cost, strong_first, e.name)
    return (e.cost, standard_first, e.name)


def _why(difficulty: Difficulty, e: EngineInfo, shot) -> str:
    parts: list[str] = []
    if difficulty == "hard":
        parts.append("难镜头用强模型更稳" if e.tier == "strong" else "没有可用的强模型，退而用这个")
    elif difficulty == "medium":
        parts.append("中等难度，先用便宜的" if e.tier == "standard" else "中等难度，强模型里最便宜的")
    else:
        parts.append("简单镜头，便宜的就够")
    parts.append(RUNTIME_TEXT.get(e.runtime, e.runtime))
    if getattr(shot, "dialogue", None):
        parts.append("有声")
    if getattr(shot, "use_end_frame", False):
        parts.append("支持尾帧")
    if refs_mode(shot):
        parts.append("支持参考图直出")
    return "，".join(parts)


def advise_shot(shot, engines: Iterable[EngineInfo], current: str | None) -> ShotAdvice:
    """一个镜头的引擎建议。engines：已注册的视频引擎；current：镜头实际要用的引擎（镜头上的，没选就是项目的）。"""
    engines = list(engines)
    score, reasons = shot_difficulty(shot)
    diff = difficulty_of(score)
    reqs = shot_requirements(shot)
    if refs_mode(shot):
        reasons.append("参考图直出：要支持参考图直出的引擎")
    if getattr(shot, "use_end_frame", False):
        reasons.append("要用尾帧：要支持尾帧的引擎")
    checks = [check_engine(shot, e) for e in engines]
    by_name = {e.name: e for e in engines}
    check_of = {c.engine: c for c in checks}

    usable = [e for e in engines if e.ready and check_of[e.name].ok]
    ranked = sorted(usable, key=lambda e: _rank_key(diff, e))
    recommended = [Recommendation(engine=e.name, label=e.label, why=_why(diff, e, shot)) for e in ranked[:TOP_N]]

    cur = by_name.get(current) if current else None
    waste = waste_seconds(cur.caps, float(getattr(shot, "duration", 0) or 0), getattr(shot, "beats", None)) \
        if cur is not None else None
    if waste:
        # 固定时长引擎（Gemini 固定 10 秒、Muse 只认 3 / 5 / 10 秒）：镜头比一次生成短，多出来的白出、还占额度
        unit, lost = waste
        reasons.append(f"固定时长引擎会浪费 {lost:g} 秒（{cur.label} 每次出 {unit:g} 秒，"
                       f"这一镜只用 {float(shot.duration):g} 秒；可以并进下一镜做成多拍镜头）")
    if current and cur is None:
        current_ok, current_problems = False, [f"引擎 {current} 不存在或没有启用"]
    elif cur is None:
        current_ok, current_problems = False, ["没有选视频引擎（镜头和项目都没选）"]
    else:
        c = check_of[cur.name]
        current_ok, current_problems = c.ok, list(c.problems)

    switch_reason = _switch_reason(diff, cur, current_ok, current_problems, ranked)
    return ShotAdvice(shot_id=int(getattr(shot, "id", 0) or 0), code=str(getattr(shot, "code", "") or ""),
                      difficulty=diff, score=score, reasons=reasons, requirements=reqs,
                      current_engine=current, current_ok=current_ok, current_problems=current_problems,
                      recommended=recommended, suggest_switch=switch_reason is not None,
                      switch_reason=switch_reason, checks=checks)


def _switch_reason(diff: Difficulty, cur: EngineInfo | None, current_ok: bool, problems: list[str],
                   ranked: list[EngineInfo]) -> str | None:
    """要不要建议换引擎、为什么；不用换时 None。没有可推荐的引擎时也不建议换（换不了）。"""
    if not ranked:
        return None
    best = ranked[0]
    if cur is not None and best.name == cur.name:
        return None
    if not current_ok:
        return f"当前引擎{'：' + problems[0] if problems else '不满足这一镜的要求'}"
    if cur is None or not cur.ready:
        return f"当前引擎现在用不了：{(cur.note if cur else None) or '没有说明'}"
    if diff == "hard" and cur.tier == "standard" and best.tier == "strong":
        return "难镜头用标准档引擎容易废片，换强模型更省重抽"
    if diff == "easy" and cur.cost > best.cost and cur.cost >= RUNTIME_COST["runninghub"]:
        return "简单镜头不用花钱跑，换便宜的就够"
    return None


def advise_episode(episode_id: int, shots: Iterable, engines: Iterable[EngineInfo],
                   default_engine: str | None) -> EpisodeAdvice:
    """一集所有镜头（按顺序）的引擎建议；镜头上没选引擎时按项目的 default_engine。"""
    engines = list(engines)
    out = [advise_shot(sh, engines, getattr(sh, "video_engine", None) or default_engine) for sh in shots]
    return EpisodeAdvice(episode_id=episode_id, shots=out, switch_count=sum(1 for a in out if a.suggest_switch))
