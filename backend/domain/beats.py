"""固定时长视频引擎和多拍镜头（设计文档 docs/specs/2026-10-12-fixed-length-engines-design.md 第 1 步）。

- 生成单元：固定 / 只认几档时长的视频引擎（caps.duration_control 是 fixed / prompt）一次生成出多长。
  Gemini 网页固定 10 秒；Muse 只认 3 / 5 / 10 秒（取能覆盖镜头的最小档）。
- 多拍镜头（Shot.beats）：一次生成里按时间戳切 2–4 拍，拍与拍首尾相接、覆盖整个镜头时长。
  分镜 LLM 输出（llm/schemas.py 的 BeatOut / ShotOut 校验）、镜头抽屉手改（PATCH）、
  「并进下一镜做成多拍镜头」都走这里的校验，规则一处说清。
- 浪费提醒：镜头比生成单元短 ≥ WASTE_MIN 秒、又不是多拍镜头时，多出来的秒数白出（还占每日额度）。
纯函数，不碰数据库。
"""
from __future__ import annotations

import math
from typing import Any

# 项目的「固定时长视频引擎怎么用满」（Project.fixed_engine_pack）
PACK_BEATS = "beats"     # 多拍镜头（默认）
PACK_GROUP = "group"     # 组镜拆分：一次生成拆给几个镜头（domain/clip_groups.py）
PACK_OFF = "off"         # 老行为：一镜一生成
PACKS = (PACK_BEATS, PACK_GROUP, PACK_OFF)

BEATS_MIN, BEATS_MAX = 2, 4     # 每个多拍镜头 2–4 拍
BEAT_MIN_SECONDS = 1.5          # 每拍至少这么长（提示词要求 2–4 秒，校验放宽一点）
BEAT_TOLERANCE = 0.2            # 拍与拍首尾相接、覆盖 [0, 时长] 的允许误差
WASTE_MIN = 1.5                 # 镜头比生成单元短这么多秒才提醒浪费
DEFAULT_UNIT_CAP = 10.0         # 只认几档的引擎（prompt），分镜默认取不超过 10 秒的最大一档

# 视频提示词里景别的英文说法（视频提示词全英文）
SHOT_SIZE_EN = {"大特写": "Extreme close-up", "特写": "Close-up", "近景": "Medium close-up",
                "中景": "Medium shot", "全景": "Wide shot", "远景": "Wide shot"}


def effective_pack(pack: str | None) -> str:
    """项目选项实际生效的模式（老项目没有这个字段时按多拍镜头）。"""
    if pack in (PACK_GROUP, PACK_OFF):
        return pack
    return PACK_BEATS


def is_fixed_length(caps) -> bool:
    """时长不能精确控制（fixed：总是 default_seconds；prompt：只能写进提示词、只认几档）。"""
    return getattr(caps, "duration_control", "param") in ("fixed", "prompt")


def generation_unit(caps, need: float | None = None) -> float | None:
    """固定 / 只认几档时长的引擎一次生成出多长；能精确控时长（param）的引擎返回 None。

    fixed：引擎的固定时长（default_seconds）。prompt：给了 need（镜头时长）时取能覆盖它的最小档
    （和 BrowserVideoEngine.ask_seconds 同一个算法），没给时取不超过 10 秒的最大档（分镜按这个排多拍镜头）。"""
    if not is_fixed_length(caps):
        return None
    if caps.duration_control == "fixed":
        return float(caps.default_seconds)
    allowed = sorted(float(x) for x in (getattr(caps, "allowed_seconds", ()) or ()))
    if not allowed:   # 提示词里写多少秒都行（连续）：要多长出多长
        top = float(caps.max_seconds)
        return min(max(float(need), 1.0), top) if need is not None else min(float(caps.default_seconds), top)
    if need is not None:
        return next((x for x in allowed if x >= float(need) - 1e-6), allowed[-1])
    under = [x for x in allowed if x <= DEFAULT_UNIT_CAP + 1e-6]
    return under[-1] if under else allowed[0]


def snap_seconds(value: float, allowed, lo: float = 0.0) -> float:
    """时长对齐到引擎认的档位（最近的一档，一样近取短的），但不短于 lo（台词说完）。"""
    opts = sorted(float(x) for x in allowed or ())
    if not opts:
        return float(value)
    ok = [x for x in opts if x >= lo - 1e-6] or [opts[-1]]
    return min(ok, key=lambda x: (abs(x - float(value)), x))


def waste_seconds(caps, duration: float, beats=None) -> tuple[float, float] | None:
    """(生成单元, 浪费秒数)：固定时长引擎、不是多拍镜头、镜头比一次生成短 ≥ WASTE_MIN 秒时才有；否则 None。"""
    if beats or not is_fixed_length(caps):
        return None
    unit = generation_unit(caps, float(duration or 0))
    if unit is None:
        return None
    waste = round(unit - float(duration or 0), 1)
    return (unit, waste) if waste >= WASTE_MIN - 1e-6 else None


def waste_text(caps, duration: float, unit: float, waste: float) -> str:
    """浪费提醒的一句话（镜头抽屉、引擎建议共用）。"""
    if getattr(caps, "duration_control", "") == "fixed":
        head = f"这个引擎每次固定出 {unit:g} 秒"
    else:
        allowed = " / ".join(f"{float(x):g}" for x in getattr(caps, "allowed_seconds", ()) or ())
        head = f"这个引擎只认 {allowed} 秒，这一镜要按 {unit:g} 秒出" if allowed else f"这个引擎每次出 {unit:g} 秒"
    return f"{head}，这一镜只用 {float(duration):g} 秒，会浪费 {waste:g} 秒"


def episode_shot_count(target_seconds: float, unit: float) -> int:
    """多拍镜头模式整集几个镜头：每个镜头是一次生成，向上取整（不让每镜超过一个生成单元）。"""
    return max(1, math.ceil(float(target_seconds) / max(float(unit), 0.1) - 1e-6))


def check_beats(beats: Any, duration: float) -> list[dict] | None:
    """校验并整理多拍镜头的每一拍；不合规时抛 ValueError（中文，LLM 重试 / 接口报错直接用）。
    None / 空列表 = 不是多拍镜头。"""
    if beats is None or beats == []:
        return None
    if not isinstance(beats, list):
        raise ValueError("beats 要写成数组：[{start, end, shot_size, camera_move, action}]")
    if not BEATS_MIN <= len(beats) <= BEATS_MAX:
        raise ValueError(f"beats 要有 {BEATS_MIN}–{BEATS_MAX} 拍，现在是 {len(beats)} 拍")
    dur = float(duration)
    out: list[dict] = []
    for i, b in enumerate(beats, start=1):
        if hasattr(b, "model_dump"):
            b = b.model_dump()
        if not isinstance(b, dict):
            raise ValueError(f"第 {i} 拍要写成对象：{{start, end, shot_size, camera_move, action}}")
        try:
            start, end = float(b.get("start")), float(b.get("end"))
        except (TypeError, ValueError):
            raise ValueError(f"第 {i} 拍的 start / end 要写成秒数") from None
        if end - start < BEAT_MIN_SECONDS - 1e-6:
            raise ValueError(f"第 {i} 拍只有 {end - start:.1f} 秒，每拍至少 {BEAT_MIN_SECONDS:g} 秒"
                             "（建议 2–4 秒）；拍太多就合并几拍")
        action = str(b.get("action") or "").strip()
        if not action:
            raise ValueError(f"第 {i} 拍要写 action（这一拍画面里发生什么）")
        out.append({"start": round(start, 2), "end": round(end, 2),
                    "shot_size": str(b.get("shot_size") or "").strip(),
                    "camera_move": str(b.get("camera_move") or "").strip(), "action": action})
    if abs(out[0]["start"]) > BEAT_TOLERANCE:
        raise ValueError(f"第 1 拍要从 0 秒开始，现在从 {out[0]['start']:g} 秒开始")
    for i in range(1, len(out)):
        gap = out[i]["start"] - out[i - 1]["end"]
        if abs(gap) > BEAT_TOLERANCE:
            raise ValueError(f"第 {i + 1} 拍的 start={out[i]['start']:g} 要接上第 {i} 拍的 "
                             f"end={out[i - 1]['end']:g}（拍与拍首尾相接，不留空、不重叠）")
    last = out[-1]["end"]
    if abs(last - dur) > BEAT_TOLERANCE:
        raise ValueError(f"最后一拍要在镜头结束时结束：end={last:g}，镜头 duration={dur:g}"
                         "（所有拍加起来正好覆盖整个镜头）")
    # 误差范围内的缝对齐：首拍从 0 开始，后一拍从前一拍结束处开始，最后一拍结束在镜头时长
    out[0]["start"] = 0.0
    for i in range(1, len(out)):
        out[i]["start"] = out[i - 1]["end"]
    out[-1]["end"] = round(dur, 2)
    return out


def scale_beats(beats: list[dict] | None, duration: float) -> list[dict] | None:
    """镜头时长被按预算缩放后，拍按比例跟着缩放（首尾相接、覆盖新时长）。"""
    if not beats:
        return None
    old = float(beats[-1]["end"]) or 1.0
    f = float(duration) / old
    out = [{**b, "start": round(float(b["start"]) * f, 2), "end": round(float(b["end"]) * f, 2)}
           for b in beats]
    out[0]["start"] = 0.0
    for i in range(1, len(out)):
        out[i]["start"] = out[i - 1]["end"]
    out[-1]["end"] = round(float(duration), 2)
    return out


def beat_boundaries(beats) -> list[float]:
    """拍与拍之间的切点（不含 0 和结尾）：质检时这些位置附近的剪切是预期的。"""
    return [float(b["end"]) for b in (beats or [])[:-1] if isinstance(b, dict) and "end" in b]


def stamp(t: float) -> str:
    """时间戳 00:03（时间戳提示词用，按整秒四舍五入）。"""
    s = int(math.floor(float(t) + 0.5))   # 四舍五入（不用 round 的银行家舍入：6.5 → 7）
    return f"{s // 60:02d}:{s % 60:02d}"
