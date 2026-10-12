"""组镜拆分（设计文档 docs/specs/2026-10-12-fixed-length-engines-design.md §3.3，固定时长视频引擎第 2 步）。

固定时长 / 只认几档的视频引擎（Gemini 网页固定 10 秒、Muse 只认 3 / 5 / 10 秒）一次生成用满：同一段落里相连的
几个普通镜头（各自 2–8 秒，各有景别、动作、台词）一次生成——按时间戳提示词每个成员一段，出来一条视频，
导出时按偏移拆给各个成员。

- 组的认定（Shot.clip_group）：按集里的顺序，**相连**、组号相同、同一段落的一串镜头才是一组；只剩一个的不算组
  （normalize_groups 会把它清掉）。组里排第一的是「主镜」（lead）：视频任务、视频候选都挂在主镜上；
  其他成员不单独出视频，界面上显示主镜选定的视频（从自己的偏移开始）。
- 首帧可选：组镜用主镜的首帧（首帧模式）或参考图（主镜选了参考图直出）；成员的首帧出不出都行，不挡视频。
- 生成方式、视频引擎、接上一镜跟主镜；成员不能接上一镜（和上一镜本来就是同一次生成）。
- 重拍组里任一镜头 = 重出整组（runs.submit_chain 把成员换成主镜）。
- 导出：每个成员 (主镜视频, 起点, 长度)。起点按成员时长累加，对齐到检测到的剪切点（±BOUNDARY_SNAP 秒内取最近的）。
纯函数为主；group_of / video_owner 查一次数据库。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

GROUP_PACE = 4.0          # 组镜模式没设镜头节奏时每镜几秒（单镜头 3–8 秒，见设计文档 §2）
GROUP_SHOT_MAX = 8.0      # 组镜模式每镜最长（一次生成要拆给 ≥ 2 个镜头才划算）
UNIT_TOLERANCE = 0.2      # 组总时长超过生成单元这么多以内不算超
BOUNDARY_SNAP = 0.6       # 导出时成员起点对齐到 ±这么多秒内最近的剪切点
MIN_PIECE = 0.5           # 对齐后每个成员至少留这么长（剪切点挤在一起时不对齐）


@dataclass
class ClipGroup:
    """一组镜头（按集里的顺序）。members[0] 是主镜。"""
    members: list[Any]
    offsets: list[float] = field(default_factory=list)   # 各成员在这次生成里的计划起点（时长累加）

    def __post_init__(self) -> None:
        acc, out = 0.0, []
        for m in self.members:
            out.append(round(acc, 3))
            acc += float(m.duration or 0)
        self.offsets = out

    @property
    def lead(self):
        return self.members[0]

    @property
    def ids(self) -> list[int]:
        return [m.id for m in self.members]

    @property
    def codes(self) -> list[str]:
        return [m.code for m in self.members]

    @property
    def total(self) -> float:
        return round(sum(float(m.duration or 0) for m in self.members), 3)

    def index(self, shot_id: int) -> int:
        """1 起的位置（主镜是 1）。"""
        return self.ids.index(shot_id) + 1

    def offset(self, shot_id: int) -> float:
        return self.offsets[self.ids.index(shot_id)]


def _ordered(shots) -> list:
    return sorted(shots, key=lambda x: (x.order, x.id))


def runs(shots) -> list[list]:
    """按集里的顺序，相连、组号相同、同一段落的一串镜头（含只有一个的）。"""
    out: list[list] = []
    last = None   # 上一个镜头（不管在不在组里）：中间隔了别的镜头就不相连
    for sh in _ordered(shots):
        g = getattr(sh, "clip_group", None)
        if g is not None:
            if (last is not None and getattr(last, "clip_group", None) == g
                    and last.scene_block_id == sh.scene_block_id):
                out[-1].append(sh)
            else:
                out.append([sh])
        last = sh
    return out


def episode_groups(shots) -> dict[int, ClipGroup]:
    """镜头 id → 它所在的组（只算 ≥ 2 个成员的）。"""
    out: dict[int, ClipGroup] = {}
    for run in runs(shots):
        if len(run) >= 2:
            g = ClipGroup(run)
            for m in run:
                out[m.id] = g
    return out


def normalize_groups(shots) -> bool:
    """整理组号：只剩一个的清掉；两串不相连的用了同一个组号时，后面那串换一个新号。返回有没有改。"""
    changed = False
    seen: set[int] = set()
    nxt = next_group_no(shots)
    for run in runs(shots):
        if len(run) < 2:
            run[0].clip_group = None
            changed = True
            continue
        g = run[0].clip_group
        if g in seen:
            for m in run:
                m.clip_group = nxt
            nxt += 1
            changed = True
        seen.add(run[0].clip_group)
    return changed


def next_group_no(shots) -> int:
    return max((int(sh.clip_group) for sh in shots if getattr(sh, "clip_group", None) is not None),
               default=0) + 1


def group_of(s, shot) -> ClipGroup | None:
    """这一镜所在的组（不在组里返回 None）。"""
    if getattr(shot, "clip_group", None) is None:
        return None
    from sqlalchemy import select

    from backend.domain.models import Shot

    shots = list(s.scalars(select(Shot).where(Shot.episode_id == shot.episode_id)))
    return episode_groups(shots).get(shot.id)


def video_owner(s, shot):
    """这一镜的视频挂在谁身上：组镜成员 → 主镜，其余是自己。"""
    g = group_of(s, shot)
    return g.lead if g is not None else shot


# ------------------------------------------------------------ 分镜：自动分组
def assign_groups(durations: list[float], unit: float, tol: float = UNIT_TOLERANCE) -> list[int | None]:
    """一段里相连的镜头怎么分组（每组总时长 ≤ 生成单元）：返回每镜的组序号（同组同号，从 0 起），
    单独成一次生成的为 None。

    按「生成次数最少 → 单独生成的镜头最少 → 各组尽量填满（总时长的平方和最大）」取最优的连续切分（动态规划）。"""
    n = len(durations)
    if n == 0:
        return []
    cap = float(unit) + tol
    INF = (10 ** 9, 10 ** 9, 0.0)
    best: list[tuple[int, int, float]] = [INF] * (n + 1)
    cut: list[int] = [0] * (n + 1)
    best[0] = (0, 0, 0.0)
    for j in range(1, n + 1):
        total = 0.0
        for i in range(j - 1, -1, -1):
            total += float(durations[i])
            if j - i > 1 and total > cap + 1e-9:
                break
            gens, singles, fill = best[i]
            cand = (gens + 1, singles + (1 if j - i == 1 else 0), fill - total * total)
            if cand < best[j]:
                best[j], cut[j] = cand, i
    spans: list[tuple[int, int]] = []
    j = n
    while j > 0:
        spans.append((cut[j], j))
        j = cut[j]
    spans.reverse()
    out: list[int | None] = [None] * n
    k = 0
    for a, b in spans:
        if b - a >= 2:
            for i in range(a, b):
                out[i] = k
            k += 1
    return out


# ------------------------------------------------------------ 生成：一组当成一个多拍镜头
def group_segments(members) -> list[dict]:
    """时间戳分段（和多拍镜头的拍同结构）：每个成员一段（成员本身是多拍镜头时按拍平移）。
    动作用英文运动提示词（时间戳提示词全英文），没有才用中文动作。"""
    out: list[dict] = []
    off = 0.0
    for m in members:
        if m.beats:
            out += [{**b, "start": round(float(b["start"]) + off, 2),
                     "end": round(float(b["end"]) + off, 2)} for b in m.beats]
        else:
            out.append({"start": round(off, 2), "end": round(off + float(m.duration), 2),
                        "shot_size": m.shot_size or "", "camera_move": m.camera_move or "",
                        "action": (m.motion_prompt or "").strip() or (m.action or "").strip()
                        or "continue"})
        off += float(m.duration)
    return out


def merged_dialogue(members) -> list[dict]:
    """各成员的台词按成员偏移挪到整组的时间轴上。"""
    out: list[dict] = []
    off = 0.0
    for m in members:
        for d in m.dialogue or []:
            if isinstance(d, dict):
                out.append({**d, "start": round(float(d.get("start") or 0) + off, 2),
                            "end": round(float(d.get("end") or 0) + off, 2)})
        off += float(m.duration)
    return out


def _union(items, key) -> list:
    out, seen = [], set()
    for x in items:
        k = key(x)
        if k not in seen:
            seen.add(k)
            out.append(x)
    return out


NO_MUSIC_TAIL = "No background music."


def _join_audio(parts: list[str]) -> str:
    """几段英文音效提示词接起来；「No background music.」只留一句（在最后）。"""
    texts = [p.strip() for p in parts if p and p.strip()]
    tail = any(t.endswith(NO_MUSIC_TAIL) for t in texts)
    texts = [t[: -len(NO_MUSIC_TAIL)].rstrip() if t.endswith(NO_MUSIC_TAIL) else t for t in texts]
    texts = list(dict.fromkeys(t for t in texts if t))
    return " ".join(texts + ([NO_MUSIC_TAIL] if tail else []))


class GroupShot:
    """整组当成一个多拍镜头给视频生成 / 过期判断用（只读）：时长 = 组总时长，beats = 每个成员一段，
    台词按偏移合并，出场角色 / 道具取并集，音效提示词接起来；其余（id、code、首帧提示词、景别、生成方式、
    视频引擎、接上一镜……）都是主镜的。输入哈希因此覆盖所有成员：成员改了时长、动作、台词，整组视频就过期。"""

    def __init__(self, group: ClipGroup):
        self._group = group
        self._lead = group.lead
        ms = group.members
        self.duration = group.total
        self.beats = group_segments(ms)
        self.dialogue = merged_dialogue(ms)
        self.cast = _union([c for m in ms for c in (m.cast or [])],
                           lambda c: c.get("character_id") if isinstance(c, dict) else c)
        self.props = _union([p for m in ms for p in (m.props or [])], lambda p: p)
        self.audio_prompt = _join_audio([m.audio_prompt or "" for m in ms])
        self.action = "；".join(x for x in ((m.action or "").strip() for m in ms) if x)
        self.clip_members = list(group.ids)

    @property
    def group(self) -> ClipGroup:
        return self._group

    def __getattr__(self, name: str):
        return getattr(self._lead, name)


def group_shot(group: ClipGroup) -> GroupShot:
    return GroupShot(group)


def video_shot(s, shot):
    """出视频 / 判断视频过期时用的「镜头」：组镜主镜返回整组（GroupShot），其余原样返回。
    成员不单独出视频，调用方先用 video_owner 换成主镜。"""
    g = group_of(s, shot)
    if g is None or g.lead.id != shot.id:
        return shot
    return GroupShot(g)


# ------------------------------------------------------------ 导出：成员起点对齐剪切点
def snap_boundaries(planned: list[float], cuts: list[float], total: float | None = None,
                    window: float = BOUNDARY_SNAP) -> list[float]:
    """成员之间的切点（计划值，不含 0）对齐到 ±window 秒内最近的检测剪切点；对齐后仍要递增、每段至少
    MIN_PIECE 秒，不满足的那个切点按计划值。total：视频总长（切点不能越过）。"""
    out: list[float] = []
    prev = 0.0
    cs = sorted(float(c) for c in cuts or [])
    for i, p in enumerate(planned):
        nxt = planned[i + 1] if i + 1 < len(planned) else (total if total is not None else None)
        near = [c for c in cs if abs(c - p) <= window + 1e-9]
        pick = min(near, key=lambda c: (abs(c - p), c)) if near else p
        ok = pick - prev >= MIN_PIECE - 1e-9 and (nxt is None or nxt - pick >= MIN_PIECE - 1e-9)
        v = pick if ok else p
        out.append(round(v, 3))
        prev = v
    return out
