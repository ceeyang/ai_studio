"""网页出图的错误。都是 JobError：任务失败时原样显示给用户，所以文案要说清楚怎么办。"""
from __future__ import annotations

from typing import Literal

from backend.queue.errors import JobError

Kind = Literal["not_logged_in", "quota", "refused", "timeout", "no_image", "page", "browser"]

LOGIN_HINT = "在设置页点“打开浏览器登录”"


class BrowserError(JobError):
    def __init__(self, kind: Kind, message: str):
        super().__init__(message)
        self.kind = kind


def excerpt(text: str, n: int = 160) -> str:
    t = " ".join((text or "").split())
    return t if len(t) <= n else t[: n - 1] + "…"
