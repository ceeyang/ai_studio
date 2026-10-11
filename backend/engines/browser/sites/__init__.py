"""各网站的选择器与文案。网站改版导致出图失败时，先看对应文件里的注释改选择器。

能出视频的网站在 VIDEO_SITES（只走浏览器扩展）。claude 只做文本（网页文本模型 web_claude，见 backend/engines/browser/chat.py），不接出图任务。
"""
from __future__ import annotations

from backend.engines.browser.sites.base import SiteSpec
from backend.engines.browser.sites.chatgpt import CHATGPT
from backend.engines.browser.sites.claude import CLAUDE
from backend.engines.browser.sites.gemini import GEMINI
from backend.engines.browser.sites.muse import MUSE

SITES: dict[str, SiteSpec] = {s.key: s for s in (CHATGPT, GEMINI, MUSE, CLAUDE)}
IMAGE_SITES: tuple[str, ...] = (CHATGPT.key, GEMINI.key, MUSE.key)
VIDEO_SITES: tuple[str, ...] = (GEMINI.key, MUSE.key)

__all__ = ["SITES", "IMAGE_SITES", "VIDEO_SITES", "SiteSpec", "CHATGPT", "CLAUDE", "GEMINI", "MUSE"]
