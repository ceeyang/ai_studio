"""成片时间轴：每镜用多少秒。

H3 的帧数落在 17k+5 网格上，出片常比分镜时长长零点几秒。合成时把多出来的尾巴裁掉，
但只在台词肯定已经说完时才裁：分镜里最后一句的 end 加 SPEECH_TAIL 收尾仍不超过裁剪点。
台词时间是分镜时的估计值，H3 实际说话会有出入，所以宁可留长也不切台词。
"""
from __future__ import annotations

TRIM_TOLERANCE = 0.15   # 裁剪点 = 分镜时长 + 这点余量
SPEECH_TAIL = 0.3       # 最后一句台词结束后至少留这么多
FRAME = 1 / 24


def trim_limit(clip_seconds: float, shot_seconds: float, dialogue: list[dict], *,
               tolerance: float = TRIM_TOLERANCE, tail: float = SPEECH_TAIL) -> float | None:
    """这一镜最多用多少秒；不需要或不能裁时返回 None（整段用）。"""
    cut = float(shot_seconds) + tolerance
    if clip_seconds <= cut + FRAME:
        return None
    last = max((float(d.get("end") or 0) for d in dialogue or []), default=0.0)
    if last + tail > cut:
        return None
    return cut
