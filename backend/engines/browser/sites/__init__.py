"""各网站的选择器与文案。网站改版导致出图失败时，先看对应文件里的注释改选择器。

claude 只做文本（网页文本模型 web_claude，见 backend/engines/browser/chat.py），不接出图任务。
"""
from __future__ import annotations

from backend.engines.browser.sites.base import SiteSpec
from backend.engines.browser.sites.chatgpt import CHATGPT
from backend.engines.browser.sites.claude import CLAUDE
from backend.engines.browser.sites.gemini import GEMINI

SITES: dict[str, SiteSpec] = {s.key: s for s in (CHATGPT, GEMINI, CLAUDE)}
IMAGE_SITES: tuple[str, ...] = (CHATGPT.key, GEMINI.key)

__all__ = ["SITES", "IMAGE_SITES", "SiteSpec", "CHATGPT", "CLAUDE", "GEMINI"]
