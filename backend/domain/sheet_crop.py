"""三视图设定图切图：设定图是纯色背景上一排人物（全身 4 个 / 头像 3 个），按列投影找出每个人物。

做法：用图片上沿和左右两边（上半段）的像素估出背景色，和背景差得多的像素算前景；图片上半部分
里每一列前景像素够多就算"有人"，连续的列是一个人物（窄缝并回去，太窄的噪点丢掉）。找到的人物数和预期一致才按人物切，
否则（多画了一个人、背景不干净）退回等宽切分，由用户换一张或手动"重新切图"。

全身视角的格子用同一个上下范围，保证几个视角比例一致。
"""
from __future__ import annotations

from typing import Literal

import numpy as np
from PIL import Image

Box = tuple[int, int, int, int]  # (x0, y0, x1, y1)，右下不含
Method = Literal["auto", "segment", "equal"]

BG_TOL = 28            # 和背景色差多少算前景（0–255，按通道最大差）
BORDER_CLEAN = 0.6     # 边框像素至少这么多和背景色一致，才算干净背景（人物可能贴着上沿或两边）
MERGE_GAP = 0.015      # 小于这个宽度比例的空隙并回同一个人物（手臂和身体之间的缝）
FORCE_MERGE_GAP = 0.04  # 人物多了时，只合并小于这个比例的空隙
MIN_RUN = 0.012        # 窄于这个宽度比例的前景列当噪点
PAD_Y = 0.03
UPPER = 0.6           # 找人物用图片上面这一部分


def equal_boxes(w: int, h: int, n: int) -> list[Box]:
    return [(w * i // n, 0, w * (i + 1) // n, h) for i in range(n)]


def _mask(arr: np.ndarray) -> np.ndarray | None:
    """前景掩码；背景不干净时返回 None。

    背景色只用上沿和左右两边的上半段估：下半段常有地面、脚下影子，头像设定图的肩膀还会贴到两边。"""
    h, w, _ = arr.shape
    bt, bs, upper = max(2, h // 50), max(2, w // 100), int(h * UPPER)
    border = np.concatenate([arr[:bt].reshape(-1, 3), arr[:upper, :bs].reshape(-1, 3),
                             arr[:upper, -bs:].reshape(-1, 3)])
    bg = np.median(border, axis=0)
    if (np.abs(border - bg).max(axis=1) <= BG_TOL).mean() < BORDER_CLEAN:
        return None
    return np.abs(arr - bg).max(axis=2) > BG_TOL


def _runs(flags: np.ndarray) -> list[list[int]]:
    runs: list[list[int]] = []
    start = None
    for x, on in enumerate(flags):
        if on and start is None:
            start = x
        elif not on and start is not None:
            runs.append([start, x])
            start = None
    if start is not None:
        runs.append([start, len(flags)])
    return runs


def _merge(runs: list[list[int]], max_gap: float) -> list[list[int]]:
    out: list[list[int]] = []
    for r in runs:
        if out and r[0] - out[-1][1] < max_gap:
            out[-1][1] = r[1]
        else:
            out.append(list(r))
    return out


def _segment(arr: np.ndarray, n: int) -> list[Box] | None:
    h, w, _ = arr.shape
    mask = _mask(arr)
    if mask is None:
        return None
    # 只用上半部分分人物：头和上身之间总有空隙，下半部分的地面影子、挨着的肩膀会把人连起来
    cols = mask[:int(h * UPPER)].sum(axis=0)
    flags = cols > max(3, int(h * 0.01))
    if flags.mean() > 0.85:
        return None
    runs = [r for r in _merge(_runs(flags), w * MERGE_GAP) if r[1] - r[0] >= w * MIN_RUN]
    while len(runs) > n:
        gaps = [b[0] - a[1] for a, b in zip(runs, runs[1:])]
        i = int(np.argmin(gaps))
        if gaps[i] >= w * FORCE_MERGE_GAP:
            break
        runs[i:i + 2] = [[runs[i][0], runs[i + 1][1]]]
    if len(runs) != n:
        return None
    rows = mask[:, np.concatenate([np.arange(a, b) for a, b in runs])].any(axis=1)
    ys = np.flatnonzero(rows)
    pad_y = int(h * PAD_Y)
    y0, y1 = max(0, int(ys[0]) - pad_y), min(h, int(ys[-1]) + 1 + pad_y)
    pad_x = max(16, max(b - a for a, b in runs) // 2)
    mids = [(a[1] + b[0]) // 2 for a, b in zip(runs, runs[1:])]
    boxes: list[Box] = []
    for i, (a, b) in enumerate(runs):
        lo = mids[i - 1] if i > 0 else 0
        hi = mids[i] if i < len(mids) else w
        boxes.append((max(lo, a - pad_x), y0, min(hi, b + pad_x), y1))
    return boxes


def crop_boxes(im: Image.Image, n: int, method: Method = "auto") -> tuple[list[Box], str]:
    """返回 n 个从左到右的切图框和实际用的方法（"segment" 按人物 / "equal" 等宽）。"""
    w, h = im.size
    if method != "equal":
        arr = np.asarray(im.convert("RGB"), dtype=np.int16)
        boxes = _segment(arr, n)
        if boxes is not None:
            return boxes, "segment"
    return equal_boxes(w, h, n), "equal"


def crop_sheet(im: Image.Image, n: int, method: Method = "auto"
               ) -> tuple[list[Image.Image], list[Box], str]:
    boxes, used = crop_boxes(im, n, method)
    return [im.crop(b) for b in boxes], boxes, used
