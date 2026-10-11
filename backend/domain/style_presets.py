"""风格预设：新建项目时选（真人写实 / 3D 写实 / 日系动漫 / 国风漫画 / 美式漫画 / 商业广告），项目设置里可改。

预设决定：
- script_visual 写风格锚（style.anchor_text）的画风方向。锚仍然只写成像风格（画风、镜头与质感、
  调色、光线软硬、材质真实感），不写场景、天气、光源、道具——校验见 pipeline/script.anchor_problems；
- 项目的负面提示词默认值（只有支持负面提示词的引擎会用）；
- 推荐的图片 / 视频引擎（只是建议：新建项目对话框里预选，用户可以改）；首帧引擎要能吃参考图才能
  锁脸、用场景图，所以写实类首帧优先 h3_image；krea2 只推荐给定妆照这一步（portrait_engines）；
- 剧本结构 / 分镜的额外写法指引（目前只有商业广告：产品特写插入镜头、结尾行动号召镜头，配合片尾卡）。

改预设不会自动改已有的视觉设定：风格锚、角色和场景外观要重新生成剧本的视觉设定才会按新预设写。
预设不进剧本 / 分镜的输入指纹（换预设不该让下一次整集运行自动重写剧本、删光镜头）。
"""
from __future__ import annotations

from dataclasses import dataclass

DEFAULT_PRESET = "live_action"


@dataclass(frozen=True)
class StylePreset:
    key: str
    label: str
    description: str           # 新建项目对话框里的一句中文说明
    style_label: str           # 写进剧本 / 分镜提示词里的风格说法（如「仿真人风格」）
    anchor_guidance: str       # script_visual：风格锚的画风方向（只写成像风格）
    negative: str              # 项目负面提示词默认值（英文）
    image_engines: tuple[str, ...]   # 推荐的图片引擎（首帧用，按优先级，取第一个可用的）
    video_engines: tuple[str, ...]   # 推荐的视频引擎
    # 推荐的定妆照引擎（设置页「角色定妆照」这一步）。首帧引擎要能吃参考图才能锁脸、用场景图，
    # krea2 吃不了参考图，但出定妆照（中性影棚半身照）质感好，所以只推荐给定妆照这一步
    portrait_engines: tuple[str, ...] = ()
    script_guidance: str = ""        # script_structure 的额外要求
    storyboard_guidance: str = ""    # storyboard 的额外要求

    def public(self) -> dict:
        return {"key": self.key, "label": self.label, "description": self.description,
                "negative": self.negative, "image_engines": list(self.image_engines),
                "video_engines": list(self.video_engines),
                "portrait_engines": list(self.portrait_engines),
                "has_storyboard_guidance": bool(self.storyboard_guidance)}


_PRESETS = (
    StylePreset(
        key="live_action", label="真人写实",
        description="像真人实拍的短剧：真实皮肤和布料质感、电影镜头感。",
        style_label="仿真人风格",
        anchor_guidance=("photorealistic live-action，真人实拍质感：写镜头与焦段（如 35mm / 50mm prime "
                         "lens）、胶片或数字质感（fine film grain）、整体调色和对比、光线软硬、"
                         "natural skin texture 和布料材质的真实感；写 natural lip color（自然唇色，"
                         "不涂唇彩、不反光）"),
        negative=("cartoon, anime, illustration, 3d render, cgi, plastic skin, doll face, "
                  "glossy lips, lipstick, deformed hands, extra fingers, watermark, logo, captions"),
        image_engines=("h3_image", "krea2", "zimage"), video_engines=("h3_video", "wan22_i2v"),
        portrait_engines=("krea2",)),
    StylePreset(
        key="cg_realistic", label="3D 写实",
        description="高精度 3D 渲染的写实角色，像游戏过场动画或 CG 短片。",
        style_label="3D 写实 CG 风格",
        anchor_guidance=("high-end photorealistic 3D CG render，写实三维渲染：写渲染质感（如 "
                         "Unreal Engine 5 style render, physically based materials, subsurface "
                         "scattering on skin）、虚拟镜头焦段与景深、整体调色和对比、光线软硬、"
                         "材质的精细度；不写成真人照片，也不写成卡通"),
        negative=("2d, flat illustration, anime, sketch, low poly, blurry textures, "
                  "deformed hands, extra fingers, text, watermark"),
        image_engines=("zimage", "h3_image", "krea2"), video_engines=("h3_video", "wan22_i2v")),
    StylePreset(
        key="anime", label="日系动漫",
        description="日本动画的赛璐璐画风：干净线条、平涂上色、大眼睛。",
        style_label="日系动漫风格",
        anchor_guidance=("Japanese anime style，日系动画：写画风（如 2D cel-shaded anime, clean "
                         "line art, flat colors with soft gradient shading）、线条粗细、上色方式、"
                         "整体调色和饱和度、光影的软硬；不写真人照片、胶片颗粒和皮肤毛孔这类写实词"),
        negative=("photorealistic, photo, 3d render, realistic skin pores, film grain, "
                  "deformed hands, extra fingers, text, watermark"),
        image_engines=("gpt_image", "gemini_image", "zimage"),
        video_engines=("wan22_i2v", "h3_video")),
    StylePreset(
        key="guofeng", label="国风漫画",
        description="中国风插画：水墨笔触、工笔线条、典雅的传统配色。",
        style_label="国风漫画风格",
        anchor_guidance=("Chinese guofeng illustration style，国风漫画：写画风（如 traditional "
                         "Chinese ink wash and gongbi line art, delicate brush strokes, rice paper "
                         "texture）、线条和笔触、传统配色（如朱红、石青、月白）与饱和度、光影软硬；"
                         "不写真人照片和胶片颗粒这类写实词"),
        negative=("photorealistic, photo, 3d render, western cartoon, neon colors, "
                  "deformed hands, extra fingers, text, watermark"),
        image_engines=("gpt_image", "gemini_image", "zimage"),
        video_engines=("wan22_i2v", "h3_video")),
    StylePreset(
        key="western_comic", label="美式漫画",
        description="美漫风格：粗黑描边、网点阴影、高对比的鲜艳色块。",
        style_label="美式漫画风格",
        anchor_guidance=("American comic book style，美式漫画：写画风（如 bold black ink outlines, "
                         "halftone dot shading, cel coloring）、描边粗细、阴影画法、高对比和高饱和"
                         "的色块、光影软硬；不写真人照片和胶片颗粒这类写实词"),
        negative=("photorealistic, photo, 3d render, anime, soft watercolor, blurry, "
                  "deformed hands, extra fingers, watermark"),
        image_engines=("gpt_image", "gemini_image", "zimage"),
        video_engines=("wan22_i2v", "h3_video")),
    StylePreset(
        key="commercial", label="商业广告",
        description="产品广告片：干净明亮的商业质感，有产品特写，结尾落到行动号召（配片尾卡）。",
        style_label="商业广告风格（真人实拍）",
        anchor_guidance=("polished commercial advertising look，商业广告实拍：写镜头与焦段（如 "
                         "85mm / 100mm macro lens for product detail）、高级干净的数字质感、明亮"
                         "通透的调色和适中对比、柔和均匀的光线质感、产品材质的光泽和真实感"),
        negative=("cartoon, anime, illustration, low quality, dull colors, cluttered, "
                  "deformed hands, extra fingers, misspelled text, watermark"),
        image_engines=("h3_image", "krea2", "zimage"), video_engines=("h3_video", "wan22_i2v"),
        portrait_engines=("krea2",),
        script_guidance=("这是一条商业广告：前 3 秒直接亮出痛点或产品；中间至少一段让产品被人使用、"
                         "效果看得见；最后一段落到行动号召（如到店、扫码、下单），台词里说出来。"),
        storyboard_guidance=("这是商业广告的分镜：产品出现的段落里给产品单独一个大特写或特写的插入镜头"
                             "（product hero shot / insert close-up），image_prompt 写清产品的颜色、"
                             "材质、包装和上面的字（画面上的字用英文双引号写出）；全片最后一段的最后"
                             "一个镜头是行动号召镜头：人物面向观众或产品居中、留出干净的画面空间，"
                             "成片后面会接片尾卡（店名、地址、联系方式），这一镜不要自己写地址电话。")),
)

PRESETS: dict[str, StylePreset] = {p.key: p for p in _PRESETS}
PRESET_KEYS = tuple(PRESETS)


def get_preset(key: str | None) -> StylePreset:
    """不认识的（或空的）按真人写实。"""
    return PRESETS.get(key or "", PRESETS[DEFAULT_PRESET])


def recommended(names: list[str], prefs: tuple[str, ...]) -> str | None:
    """推荐引擎里第一个可用的；一个都没有返回 None（前端保持原来的选择）。"""
    return next((n for n in prefs if n in names), None)


def prompt_vars(key: str | None) -> dict:
    """渲染剧本 / 分镜提示词用的预设变量。"""
    p = get_preset(key)
    return {"preset_key": p.key, "preset_label": p.label, "style_label": p.style_label,
            "preset_anchor": p.anchor_guidance, "preset_negative": p.negative,
            "preset_script": p.script_guidance, "preset_storyboard": p.storyboard_guidance}
