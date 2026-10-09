"""按高棉语时长改写中文台词（dialogue.fit_khmer 的纯逻辑部分）。

思路：H3 原生说普通话，高棉语外挂配音的长短和口型永远对不上。先量每句高棉语 TTS 念多久，
再让 LLM 把中文改写成"念起来一样长"的口语（意思不变），用普通话 TTS 实测，差距超过 ±tol 就带着
上一轮的实测结果再改，最多 rounds 轮，取最接近的一版。之后按高棉语时长重排台词时间、必要时加长镜头，
让 H3 重出视频念新中文——嘴型和说话节奏就落在高棉语配音的位置上。
"""
from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field

HEAD = 0.4     # 镜头开头到第一句至少留这么多
TAIL = 0.4     # 最后一句结束到镜头结尾至少留这么多
GAP = 0.15     # 同一镜两句之间至少隔这么多

_LATIN = re.compile(r"[A-Za-z0-9]+")
_CJK = re.compile(r"[㐀-鿿]")


def speech_chars(text: str) -> int:
    """念出来的"字数"：汉字一个算一个，英文单词/数字串按两个字算，标点不算。"""
    latin = _LATIN.findall(text or "")
    return len(_CJK.findall(text or "")) + 2 * len(latin)


@dataclass
class FitLine:
    id: str              # S01.1
    speaker: str
    zh_original: str
    km: str
    km_seconds: float
    zh_voice: str
    zh: str = ""
    zh_seconds: float = 0.0
    ratio: float = 0.0   # zh_seconds / km_seconds
    rounds: int = 0      # 第几轮改写的结果被采用（0 = 原句）
    tries: list[dict] = field(default_factory=list)

    def take(self, zh: str, seconds: float, rnd: int) -> None:
        self.tries.append({"zh": zh, "seconds": round(seconds, 3)})
        ratio = seconds / self.km_seconds if self.km_seconds > 0 else 1.0
        if not self.zh or abs(ratio - 1) < abs(self.ratio - 1):
            self.zh, self.zh_seconds, self.ratio, self.rounds = zh, seconds, ratio, rnd


def within(ratio: float, tol: float) -> bool:
    return abs(ratio - 1) <= tol + 1e-9


def target_chars(ln: FitLine) -> int:
    """按这句原文的实测语速，估算念满高棉语时长要几个字。"""
    first = ln.tries[0] if ln.tries else None
    n = speech_chars(ln.zh_original)
    cps = n / first["seconds"] if first and first["seconds"] > 0 and n else 4.5
    return max(1, int(round(ln.km_seconds * cps)))


def fit_lines(lines: list[FitLine], measure: Callable[[str, str], float],
              rewrite: Callable[[int, list[dict]], dict[str, str]], *, tol: float = 0.10,
              rounds: int = 3, log: Callable[[str], None] | None = None) -> list[FitLine]:
    """measure(中文, 普通话音色) → 秒；rewrite(第几轮, 待改的句子) → {id: 新中文}。"""
    emit = log or (lambda m: None)
    for ln in lines:
        ln.take(ln.zh_original, measure(ln.zh_original, ln.zh_voice), 0)
    for rnd in range(1, rounds + 1):
        todo = [ln for ln in lines if not within(ln.ratio, tol)]
        if not todo:
            break
        emit(f"第 {rnd} 轮改写 {len(todo)} 句：{', '.join(ln.id for ln in todo)}")
        answers = rewrite(rnd, [{"id": ln.id, "speaker": ln.speaker, "zh_original": ln.zh_original,
                                 "km": ln.km, "km_seconds": round(ln.km_seconds, 2),
                                 "target_chars": target_chars(ln), "tries": list(ln.tries)}
                                for ln in todo]) or {}
        for ln in todo:
            zh = (answers.get(ln.id) or "").strip()
            if not zh:
                continue
            sec = measure(zh, ln.zh_voice)
            ln.take(zh, sec, rnd)
            emit(f"  {ln.id} 「{zh}」 {sec:.2f}s / 高棉语 {ln.km_seconds:.2f}s"
                 f"（{sec / ln.km_seconds:.2f}）" if ln.km_seconds > 0 else f"  {ln.id} 「{zh}」")
    return lines


# ---------------------------------------------------------------- 重排时间
def _ceil01(x: float) -> float:
    return math.ceil(round(x * 10, 6)) / 10


def _layout(lens: list[float], starts: list[float] | None, head: float, gap: float):
    out, prev_end = [], None
    for i, n in enumerate(lens):
        want = starts[i] if starts is not None else 0.0
        lo = head if prev_end is None else prev_end + gap
        s = max(want, lo)
        out.append((round(s, 3), round(s + n, 3)))
        prev_end = s + n
    return out


def retime_shot(dialogue: list[dict], duration: float, max_seconds: float, *, head: float = HEAD,
                tail: float = TAIL, gap: float = GAP) -> tuple[list[dict], float, bool]:
    """每句窗口改成高棉语时长（km_seconds），尽量保留原来的起点；镜头不够长就加长（不缩短），
    超过引擎上限就把台词紧排，还放不下就封顶并返回 over=True。"""
    lens = [float(d.get("km_seconds") or max(float(d.get("end") or 0) - float(d.get("start") or 0),
                                             0.5)) for d in dialogue]
    if not lens:
        return [dict(d) for d in dialogue], float(duration), False
    spans = _layout(lens, [float(d.get("start") or 0) for d in dialogue], head, gap)
    if spans[-1][1] + tail > max_seconds + 1e-9:
        spans = _layout(lens, None, head, gap)
    need = spans[-1][1] + tail
    over = need > max_seconds + 1e-9
    new_dur = min(max(float(duration), _ceil01(need)), float(max_seconds))
    out = [{**d, "start": a, "end": b} for d, (a, b) in zip(dialogue, spans)]
    return out, round(new_dur, 2), over


# ---------------------------------------------------------------- 音频提示
_PACING = re.compile(r"\s*\[Dialogue pacing:[^\]]*\]")


def update_audio_prompt(audio: str, swaps: list[tuple[str, str]], spans: list[dict]) -> str:
    """把音频提示里写死的旧台词换成新台词，并用一段 [Dialogue pacing: …] 写明每句的起止（旧的那段先删掉）。"""
    text = _PACING.sub("", audio or "")
    for old, new in swaps:
        if old and new and old != new:
            text = text.replace(old, new)
    if spans:
        parts = "; ".join(f"line {i} from {float(d['start']):.1f}s to {float(d['end']):.1f}s"
                          for i, d in enumerate(spans, start=1))
        text = (text.rstrip() + " " if text.strip() else "") + (
            f"[Dialogue pacing: {parts}; natural unhurried pace, no speech before the first line]")
    return text
