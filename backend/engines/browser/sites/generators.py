"""生成器页网站（即梦 / Dreamina、可灵国内 / 国际）：网页出视频，只走浏览器扩展。

和聊天页网站（SiteSpec）不同，生成器页的选择器、流程只在扩展里（browser-extension/sites/jimeng.js、kling.js，
lib/flow.js 的 runGenerator）；这里只记 studio 要用的：名字、地址、登录 cookie（日志、状态、锁）。
backend/tests/test_browser_extension_js.py 比对两边的这几项。

每个网站国内、海外两个区域各一个 key（jimeng_cn / jimeng_global / kling_cn / kling_global），设置页选区域
（app_settings 的 web_region_jimeng / web_region_kling，默认国内），引擎 build() 按区域选 key。
"""
from __future__ import annotations

from dataclasses import dataclass

REGIONS: tuple[str, ...] = ("cn", "global")
REGION_LABEL = {"cn": "国内", "global": "海外"}


@dataclass(frozen=True)
class GeneratorSite:
    key: str                  # jimeng_cn …（= 扩展 SITES 里的 key）
    family: str               # jimeng / kling（区域设置按它存）
    region: str               # cn / global
    label: str                # 给人看的名字
    home: str
    generate_url: str         # 视频生成页
    host: str
    cookie_url: str
    session_cookies: tuple[str, ...]


JIMENG_CN = GeneratorSite("jimeng_cn", "jimeng", "cn", "即梦", "https://jimeng.jianying.com/",
                          "https://jimeng.jianying.com/ai-tool/generate?type=video", "jimeng.jianying.com",
                          "https://jimeng.jianying.com/", ("sessionid", "sid_tt", "uid_tt"))
JIMENG_GLOBAL = GeneratorSite("jimeng_global", "jimeng", "global", "Dreamina", "https://dreamina.capcut.com/",
                              "https://dreamina.capcut.com/ai-tool/generate?type=video", "dreamina.capcut.com",
                              "https://dreamina.capcut.com/", ("sessionid", "sid_tt", "uid_tt"))
KLING_CN = GeneratorSite("kling_cn", "kling", "cn", "可灵", "https://klingai.com/",
                         "https://klingai.com/app/video/new", "klingai.com",
                         "https://klingai.com/", ("kuaishou.ai.portal_st", "userId"))
KLING_GLOBAL = GeneratorSite("kling_global", "kling", "global", "Kling", "https://kling.ai/",
                             "https://kling.ai/app/omni/new", "kling.ai",
                             "https://kling.ai/", ("ksi18n.ai.portal_st", "userId"))

GENERATOR_SITES: dict[str, GeneratorSite] = {s.key: s for s in (JIMENG_CN, JIMENG_GLOBAL, KLING_CN, KLING_GLOBAL)}
GENERATOR_FAMILIES: tuple[str, ...] = ("jimeng", "kling")


def generator_site(family: str, region: str | None) -> GeneratorSite:
    """按区域取网站；区域不认识时用国内。"""
    reg = region if region in REGIONS else "cn"
    key = f"{family}_{reg}"
    if key not in GENERATOR_SITES:
        raise KeyError(f"没有生成器网站 {key}，可选：{', '.join(GENERATOR_SITES)}")
    return GENERATOR_SITES[key]
