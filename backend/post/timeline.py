"""成片时间轴：每镜用多少秒。

H3 的帧数落在 17k+5 网格上，出片常比分镜时长长零点几秒。合成时把多出来的尾巴裁掉，
但只在台词肯定已经说完时才裁：裁剪点 = 分镜时长 + TRIM_TOLERANCE，要求
  max(分镜里最后一句的 end, 实测音轨最后一段声音的结束) + SPEECH_TAIL ≤ 裁剪点。
只看分镜时间不够：H3 会把台词摊满整段视频（柬埔寨1号铭车 S01 分镜写 3.2s 说完，实际说到 4.8s），
所以还要用 silencedetect 量音轨（FFmpeg.sound_end）。宁可留长也不切台词。
"""
from __future__ import annotations

TRIM_TOLERANCE = 0.15   # 裁剪点 = 分镜时长 + 这点余量
SPEECH_TAIL = 0.3       # 最后一句台词结束后至少留这么多
FRAME = 1 / 24


def trim_limit(clip_seconds: float, shot_seconds: float, dialogue: list[dict], *,
               audio_end: float | None = None, tolerance: float = TRIM_TOLERANCE,
               tail: float = SPEECH_TAIL) -> float | None:
    """这一镜最多用多少秒；不需要或不能裁时返回 None（整段用）。audio_end：实测声音结束的秒数。"""
    cut = float(shot_seconds) + tolerance
    if clip_seconds <= cut + FRAME:
        return None
    last = max([float(d.get("end") or 0) for d in dialogue or []] + [float(audio_end or 0)])
    if last + tail > cut:
        return None
    return cut
