"""网页出图的错误。都是 JobError：任务失败时原样显示给用户，所以文案要说清楚怎么办。"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from backend.queue.errors import JobError

Kind = Literal["not_logged_in", "quota", "refused", "timeout", "no_image", "page", "browser"]

LOGIN_HINT = "在设置页点“打开浏览器登录”"   # cdp：studio 管的那个浏览器窗口

# extension：出图在用户平时用的浏览器里跑
EXT_DIR = Path(__file__).resolve().parents[3] / "browser-extension"
EXT_INSTALL_HINT = ("在你平时用的 Chrome/Edge 里加载 browser-extension（开发者模式→加载已解压的扩展程序）："
                    f"{EXT_DIR}")
EXT_LOGIN_HINT = "在该浏览器里登录 ChatGPT / Gemini"


class BrowserError(JobError):
    def __init__(self, kind: Kind, message: str):
        super().__init__(message)
        self.kind = kind


def excerpt(text: str, n: int = 160) -> str:
    t = " ".join((text or "").split())
    return t if len(t) <= n else t[: n - 1] + "…"
