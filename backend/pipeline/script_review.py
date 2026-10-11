"""剧本体检：花生成额度（首帧、视频）之前，先看这一集开头抓不抓人、结尾有没有卡点。

爆款 AI 短剧的经验：前 15 秒必须有钩子（身份反差 / 冲突 / 悬念），每个镜头 5–10 秒，每集结尾停在
卡点上，爽点要密。两部分：

- 规则检查（rule_findings）：不调 LLM，按分镜现算——第一句台词在第几秒、前 15 秒有没有冲突的字眼、
  前 15 秒有几个镜头、超过 10 秒的镜头、最后一镜有没有台词或强动作、全集时长和目标差多少。
  每次 GET 都现算，分镜一改就跟着变。
- LLM 体检（script.review 任务）：把剧本段落和带时间的镜头表给文本模型，按开场钩子、冲突与爽点密度、
  结尾卡点、节奏打 0–10 分，各写一句理由，再给最多 5 条落到镜头 / 段落上的改写建议。结果连同输入指纹
  存进 episodes.script_review；分镜改了以后指纹对不上，界面标「分镜改过，建议重新体检」。

只能手动点「剧本体检」：分镜完成后没有现成的「阶段完成后」钩子，自动跑还会占用网页文本模型的标签页。
文本模型走设置页「按步骤单独设置 → 剧本体检」（step = script.review），没设置时跟剧本任务。
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from backend.domain.hashing import input_hash
from backend.domain.models import Episode, utcnow
from backend.llm.schemas import REVIEW_DIMENSIONS, ScriptReviewOut
from backend.pipeline.common import load_episode
from backend.queue.errors import JobError
from backend.queue.handlers import HANDLERS

TASK = "script.review"
STEP = "script.review"
LLM_TASK = "script"          # config.yaml 的 llm.routes 按剧本任务走

HOOK_SECONDS = 15.0          # 开场钩子：前 15 秒
FIRST_LINE_WARN = 5.0        # 第一句台词超过这么多秒才出来：提醒
MIN_HOOK_SHOTS = 3           # 前 15 秒至少这么多个镜头（每镜 5 秒左右）
SHOT_MAX_SECONDS = 10.0      # 单镜超过 10 秒：节奏拖
DURATION_SLACK = 0.2         # 全集时长和目标差 20% 以上：提醒

# 冲突 / 悬念 / 反差的字眼（动作描述或台词里出现就算前 15 秒有冲突信号）。只是粗筛，细的交给 LLM 体检。
_CONFLICT = re.compile(
    r"吵|骂|吼|喊|质问|怒|打|扇|耳光|推开|推倒|摔|砸|撕|抢|威胁|逼|跪|哭|骗|偷|背叛|离婚|分手|出轨|"
    r"欠|还钱|滚|凭什么|为什么|不可能|报警|死|血|刀|枪|揭穿|冷笑|嘲|羞辱|开除|赶出|退婚|假的|"
    r"身份|真相|秘密|竟然|居然|发现|撞见|拦住|甩|摊牌|威|恨|跟踪|绑|追")
# 结尾卡点的强动作 / 悬念字眼
_CLIFF = re.compile(
    r"愣|僵|震惊|瞪|转身|推门|门开|出现|响起|来电|电话|倒下|晕|血|跪|冷笑|定格|突然|竟然|居然|"
    r"是你|你是谁|揭开|掀开|摘下|真相|身份|……|\?|？|!|！")

SEVERITIES = ("ok", "info", "warn", "error")


@dataclass
class Finding:
    key: str
    severity: str            # ok / info / warn / error
    title: str
    detail: str = ""
    shots: list[str] = field(default_factory=list)


@dataclass
class ShotLine:
    """体检用的一个镜头：编号、段落、起止时间、动作和台词。"""
    code: str
    start: float
    end: float
    action: str = ""
    shot_size: str = ""
    block: int | None = None             # 段落序号（从 1 开始），手工镜头可能没有
    dialogue: list[dict] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return round(self.end - self.start, 2)

    def lines(self) -> list[str]:
        return [str(d.get("zh") or "").strip() for d in self.dialogue if str(d.get("zh") or "").strip()]


def _num(x, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def timeline(shots) -> list[ShotLine]:
    """按镜头顺序累加时长，得到每一镜在成片里的起止秒数（不算转场重叠）。shots 是 Shot 行或同字段的对象。"""
    out: list[ShotLine] = []
    t = 0.0
    for sh in shots:
        dur = max(_num(getattr(sh, "duration", 0)), 0.0)
        block = getattr(sh, "block_no", None)
        out.append(ShotLine(code=sh.code or f"S{len(out) + 1:02d}", start=round(t, 2),
                            end=round(t + dur, 2), action=sh.action or "",
                            shot_size=getattr(sh, "shot_size", "") or "", block=block,
                            dialogue=[d for d in (sh.dialogue or []) if isinstance(d, dict)]))
        t += dur
    return out


def _fmt(sec: float) -> str:
    return f"{sec:.1f}".rstrip("0").rstrip(".")


def _first_line(tl: list[ShotLine]) -> tuple[float, ShotLine] | None:
    for sl in tl:
        starts = [_num(d.get("start")) for d in sl.dialogue if str(d.get("zh") or "").strip()]
        if starts:
            return sl.start + min(starts), sl
    return None


def rule_findings(tl: list[ShotLine], target_seconds: float) -> list[Finding]:
    """规则检查（不调 LLM）。tl 为空（还没有分镜）时只回一条说明。"""
    if not tl:
        return [Finding("no_shots", "info", "还没有分镜", "生成分镜后才能按镜头时长体检")]
    out: list[Finding] = []
    hook = [sl for sl in tl if sl.start < HOOK_SECONDS]

    # 1. 第一句台词
    first = _first_line(tl)
    if first is None:
        out.append(Finding("first_line", "warn", "全集没有一句台词",
                           "短剧靠台词推冲突：开头几秒就让人物开口（质问、争吵、反常的一句话）"))
    else:
        at, sl = first
        if at > HOOK_SECONDS:
            sev, tip = "error", "前 15 秒没人说话，观众很可能已经划走：把第一句冲突台词提到第一、二镜"
        elif at > FIRST_LINE_WARN:
            sev, tip = "warn", f"建议 {_fmt(FIRST_LINE_WARN)} 秒内就有第一句台词，开场别用空镜和交代背景"
        else:
            sev, tip = "ok", ""
        out.append(Finding("first_line", sev, f"第一句台词在第 {_fmt(at)} 秒（{sl.code}）", tip, [sl.code]))

    # 2. 前 15 秒的冲突信号
    hit = next((sl for sl in hook if _CONFLICT.search(sl.action)
                or any(_CONFLICT.search(x) or "！" in x or "!" in x for x in sl.lines())), None)
    if hit is not None:
        out.append(Finding("hook_conflict", "ok", f"前 15 秒有冲突 / 悬念（{hit.code}）", "", [hit.code]))
    else:
        out.append(Finding("hook_conflict", "warn", "前 15 秒看不出冲突或悬念",
                           "开场要有身份反差、正面冲突或反常的画面（质问、被羞辱、意外发现），"
                           "不要用交代背景、环境空镜开场", [sl.code for sl in hook]))

    # 3. 前 15 秒的镜头数
    if len(hook) < MIN_HOOK_SHOTS and tl[-1].end > HOOK_SECONDS:
        out.append(Finding("hook_shots", "warn", f"前 15 秒只有 {len(hook)} 个镜头",
                           f"开场节奏偏慢：前 15 秒建议至少 {MIN_HOOK_SHOTS} 个镜头，第一镜 2–3 秒",
                           [sl.code for sl in hook]))
    else:
        out.append(Finding("hook_shots", "ok", f"前 15 秒有 {len(hook)} 个镜头", "",
                           [sl.code for sl in hook]))

    # 4. 超过 10 秒的镜头
    long = [sl for sl in tl if sl.duration > SHOT_MAX_SECONDS]
    if long:
        out.append(Finding("long_shots", "warn", f"{len(long)} 个镜头超过 {_fmt(SHOT_MAX_SECONDS)} 秒",
                           "单镜 5–10 秒最合适：拆成两镜（加一个反应镜头或特写），或删掉次要动作",
                           [sl.code for sl in long]))
    else:
        out.append(Finding("long_shots", "ok", f"每个镜头都在 {_fmt(SHOT_MAX_SECONDS)} 秒以内"))

    # 5. 结尾卡点
    last = tl[-1]
    said = last.lines()
    if any(_CLIFF.search(x) for x in said) or _CLIFF.search(last.action):
        out.append(Finding("ending", "ok", f"最后一镜（{last.code}）停在台词或强动作上", "", [last.code]))
    elif said:
        out.append(Finding("ending", "info", f"最后一镜（{last.code}）有台词，但不太像卡点",
                           "结尾停在悬念、新的危机或没说完的话上（反问、揭露身份、突然出现的人）",
                           [last.code]))
    else:
        out.append(Finding("ending", "warn", f"最后一镜（{last.code}）没有台词也没有强动作",
                           "每集结尾要留卡点：停在一句反问、一个揭露或一个突然的动作上，不要把事情讲完、"
                           "不要用空镜收尾", [last.code]))

    # 6. 全集时长
    total = tl[-1].end
    target = float(target_seconds or 0)
    title = f"全集 {_fmt(total)} 秒 · {len(tl)} 个镜头"
    if target > 0 and abs(total - target) > target * DURATION_SLACK:
        out.append(Finding("duration", "warn", f"{title}，目标 {_fmt(target)} 秒",
                           "和目标时长差得较多：太长就删过场镜头，太短就补冲突和反应镜头"))
    else:
        out.append(Finding("duration", "ok", title + (f"（目标 {_fmt(target)} 秒）" if target > 0 else "")))
    return out


# ------------------------------------------------------------ 从数据库取体检的输入
def episode_material(ep) -> tuple[list[dict], list[ShotLine]]:
    """(剧本段落表, 镜头时间线)。段落：{"no", "location", "text"}；镜头带段落序号。"""
    block_no = {b.id: i + 1 for i, b in enumerate(ep.scene_blocks)}
    locs = {loc.id: loc.name for loc in ep.project.locations}
    blocks = [{"no": i + 1, "location": locs.get(b.location_id, ""), "text": b.text or ""}
              for i, b in enumerate(ep.scene_blocks)]

    class _Row:   # 给 timeline 一个带段落序号的镜头视图，不改 ORM 对象
        def __init__(self, sh):
            self.code, self.duration, self.action = sh.code, sh.duration, sh.action
            self.shot_size, self.dialogue = sh.shot_size, sh.dialogue
            self.block_no = block_no.get(sh.scene_block_id)

    return blocks, timeline([_Row(sh) for sh in ep.shots])


def review_fp(blocks: list[dict], tl: list[ShotLine], target_seconds: float) -> str:
    """LLM 体检的输入指纹：段落文字和镜头表（时长、动作、台词）。分镜改了指纹就变，结果标「已过期」。"""
    return input_hash("script-review", float(target_seconds or 0), blocks,
                      [[sl.code, sl.start, sl.end, sl.action, sl.lines()] for sl in tl])


def rules_view(ep) -> dict:
    """GET 用：现算的规则检查。"""
    blocks, tl = episode_material(ep)
    findings = rule_findings(tl, ep.target_seconds)
    return {"total_seconds": tl[-1].end if tl else 0.0, "shot_count": len(tl),
            "target_seconds": float(ep.target_seconds or 0),
            "findings": [asdict(f) for f in findings]}


def stored_review(ep) -> dict | None:
    """存着的 LLM 体检结果，带 stale（分镜改过了）。"""
    data = ep.script_review
    if not isinstance(data, dict) or "hook" not in data:
        return None
    blocks, tl = episode_material(ep)
    return {**{k: v for k, v in data.items() if k != "inputs"},
            "stale": data.get("inputs") != review_fp(blocks, tl, ep.target_seconds)}


# ------------------------------------------------------------ 任务


def _norm_ref(ref: str) -> str:
    r = re.sub(r"\s+", "", ref or "").upper().replace("段落", "段落 ")
    m = re.fullmatch(r"S(\d+)", r)
    return f"S{int(m.group(1)):02d}" if m else r


@HANDLERS.register(TASK)
def run_script_review(ctx, payload: dict) -> dict:
    sv = ctx.services
    episode_id = int(payload["episode_id"])
    with sv.db.session() as s:
        ep, proj = load_episode(s, episode_id)
        blocks, tl = episode_material(ep)
        if not tl:
            raise JobError("这一集还没有分镜，先生成分镜再做剧本体检")
        findings = [f for f in rule_findings(tl, ep.target_seconds) if f.severity != "ok"]
        target = float(ep.target_seconds or 0)
        system, user = sv.prompts(proj.slug).render(
            "script_review", project_title=proj.title, episode_title=ep.title or "",
            target_seconds=target, total_seconds=tl[-1].end, hook_seconds=HOOK_SECONDS,
            blocks=blocks, shots=[{**asdict(sl), "duration": sl.duration, "lines": sl.lines(),
                                   "start_s": _fmt(sl.start), "end_s": _fmt(sl.end),
                                   "said": "；".join(f"{d.get('speaker', '')}：「{d.get('zh', '')}」"
                                                    for d in sl.dialogue if d.get("zh"))}
                                  for sl in tl],
            findings=[asdict(f) for f in findings])
        fp = review_fp(blocks, tl, target)
    codes = {sl.code for sl in tl}
    nos = {b["no"] for b in blocks}

    def known(ref: str) -> bool:
        r = _norm_ref(ref)
        if r in codes:
            return True
        m = re.fullmatch(r"段落 (\d+)", r)
        return bool(m) and int(m.group(1)) in nos

    def check(out: ScriptReviewOut, *, last: bool = True) -> None:
        bad = [x.ref for x in out.suggestions if not known(x.ref)]
        if bad and not last:   # 最后一次机会不为引用写法让整次体检失败
            raise ValueError(f"suggestions 的 ref 要写镜头编号（{'、'.join(sorted(codes)[:6])}…）"
                             f"或「段落 N」，这些对不上：{'、'.join(bad)}")

    out = sv.llm.generate(LLM_TASK, system, user, ScriptReviewOut, check=check, log=ctx.log,
                          manual=payload.get("manual") or {}, key=f"script.review:{episode_id}",
                          step=STEP)
    data = out.model_dump()
    for sg in data["suggestions"]:
        sg["ref"] = _norm_ref(sg["ref"]) if known(sg["ref"]) else sg["ref"].strip()
    data.update(inputs=fp, created_at=utcnow().isoformat(timespec="seconds"), job_id=ctx.job_id)
    with sv.db.session() as s:
        ep = s.get(Episode, episode_id)
        if ep is None:
            raise JobError(f"找不到剧集 {episode_id}")
        ep.script_review = data
    scores = {k: data[k]["score"] for k in REVIEW_DIMENSIONS}
    ctx.log("剧本体检：" + "，".join(f"{REVIEW_DIMENSIONS[k]} {v}/10" for k, v in scores.items())
            + f"；改写建议 {len(data['suggestions'])} 条")
    return {"scores": scores, "suggestions": len(data["suggestions"])}
