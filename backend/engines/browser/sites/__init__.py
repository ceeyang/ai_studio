"""各网站的选择器与文案。网站改版导致出图失败时，先看对应文件里的注释改选择器。"""
from __future__ import annotations

from backend.engines.browser.sites.base import SiteSpec
from backend.engines.browser.sites.chatgpt import CHATGPT
from backend.engines.browser.sites.gemini import GEMINI

SITES: dict[str, SiteSpec] = {s.key: s for s in (CHATGPT, GEMINI)}

__all__ = ["SITES", "SiteSpec", "CHATGPT", "GEMINI"]
