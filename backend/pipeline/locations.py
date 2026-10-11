"""场景图（image.location）：给场景出一张「不出人物」的环境参考图，首帧打包参考图时排在最前面，
用来固定同一场景里各镜的布景一致。

提示词只写要什么：「empty <地方>, unoccupied」——默认的 zimage / krea2 是 cfg 1，负面提示词不起作用，
「no people」这类否定词在 cfg 1 下反而会把人招进画面。风格只取风格锚里的成像词（photo_style），
锚里写的雨夜、灯泡这类场景词留给镜头自己的提示词。
"""
from __future__ import annotations

import re
import shutil

from backend.domain.hashing import input_hash
from backend.domain.locations import add_location_image, location_spec
from backend.domain.models import Location
from backend.domain.settings import location_engine
from backend.engines.base import ImageRequest
from backend.pipeline.characters import seed_label
from backend.pipeline.keyframes import run_comfy
from backend.pipeline.prompting import join_negative, photo_style, shot_seed
from backend.queue.errors import JobError
from backend.queue.handlers import HANDLERS

TASK = "image.location"
# 支持负面提示词的引擎（RunningHub 等）才用得上；cfg 1 的引擎负面词写空，构图全靠正面描述
LOCATION_NEGATIVE = "people, person, man, woman, child, crowd, figure, character, silhouette"
_CJK = re.compile(r"[⺀-鿿가-힯豈-﫿＀-￯]")
# 剧本阶段写的是中文时段（白天/夜晚/黄昏…）：换成英文，进英文提示词
TIME_WORDS = {"白天": "daytime", "日": "daytime", "上午": "morning", "早上": "morning",
              "早晨": "early morning", "清晨": "early morning", "黎明": "dawn", "中午": "midday",
              "正午": "midday", "下午": "afternoon", "傍晚": "dusk", "黄昏": "dusk",
              "日落": "sunset", "晚上": "night", "夜": "night", "夜晚": "night", "深夜": "late night",
              "凌晨": "before dawn", "午夜": "midnight"}


def time_phrase(time_of_day: str) -> str:
    t = (time_of_day or "").strip()
    if not t:
        return ""
    if not _CJK.search(t):
        return t.lower()
    for zh in sorted(TIME_WORDS, key=len, reverse=True):
        if zh in t:
            return TIME_WORDS[zh]
    return ""


def _place(loc) -> tuple[str, str]:
    """(地方的简称, 其余描述)。简称是画面提示词第一句（去掉冠词、句首小写）；没有英文提示词时用英文名字。"""
    vp = (loc.visual_prompt or "").strip()
    if vp and not _CJK.search(vp):
        sentences = re.split(r"(?<=[.!?])\s+", vp)
        first = sentences[0].rstrip(" .!?;")
        first = re.sub(r"^(?:a|an|the)\s+", "", first, flags=re.I)
        if first and not first[1:2].isupper():
            first = first[0].lower() + first[1:]
        return first, " ".join(sentences[1:]).strip()
    name = (loc.name or "").strip()
    if name and not _CJK.search(name):
        return name, ""
    return "", ""


def location_prompt(loc, anchor: str) -> str:
    """「Empty <地方>, unoccupied, <时段>.」+ 其余环境描述 + 一句空场的正面写法 + 成像风格。"""
    place, rest = _place(loc)
    if not place:
        raise JobError(f"场景 {loc.name} 还没有英文画面提示词，先在「设定」里写一句（如 a cramped "
                       "auto repair garage with tools on the walls）")
    when = time_phrase(loc.time_of_day)
    parts = [f"Empty {place}, unoccupied{', ' + when if when else ''}."]
    if rest:
        parts.append(rest)
    # 只写正面的画面：提到演员 / 等人这类词会把人招进画面
    parts.append("Environment reference plate: the whole set is visible and still, wide eye-level "
                 "view of the bare environment.")
    style = photo_style(anchor)
    if style:
        # 写「Style」而不是「Photographic style」：动画 / 插画风格预设不能被说成照片
        parts.append(f"Style: {style}.")
    return " ".join(parts)


def _load_loc(s, loc_id: int) -> Location:
    loc = s.get(Location, loc_id)
    if loc is None:
        raise JobError(f"找不到场景 {loc_id}")
    return loc


@HANDLERS.register(TASK, gpu=True)
def run_location_image(ctx, payload: dict) -> dict:
    """出一张场景图候选。engine 不给：设置页场景图这一步 → 项目的图像引擎。"""
    sv = ctx.services
    loc_id = int(payload["location_id"])
    with sv.db.session() as s:
        loc = _load_loc(s, loc_id)
        proj, style = loc.project, loc.project.style_bible
        engine_name = location_engine(sv, proj.image_engine, payload.get("engine"))
        try:
            engine = sv.engines.image(engine_name)
        except KeyError as e:
            raise JobError(str(e)) from None
        prompt = location_prompt(loc, style.anchor_text if style else "")
        negative = join_negative(proj.negative, style.negative if style else "", LOCATION_NEGATIVE)
        seed = shot_seed(proj.seed, loc_id + 50_000, ctx.job_id)
        slug, name, aspect = proj.slug, loc.name, proj.aspect
        spec = location_spec(loc)
    call = engine.build(ImageRequest(prompt=prompt, negative=negative, aspect=aspect, seed=seed,
                                     prefix=f"studio/{slug}/location_{loc_id}"))
    ctx.log(f"场景图 {name} · {engine_name} · {seed_label(call, seed)}")
    files, run_dir = run_comfy(ctx, call, slug)
    dst = sv.media.new_media_path(slug, "location", files[0].suffix)
    shutil.copyfile(files[0], dst)
    with sv.db.session() as s:
        c = add_location_image(s, loc_id, file_path=sv.media.rel(dst), engine=engine_name,
                               params={**call.meta, "prompt": prompt, "spec": spec,
                                       "workflow": sv.media.rel(run_dir / "workflow.api.json")},
                               input_hash=input_hash("location_image", prompt, negative,
                                                     engine_name, aspect, seed),
                               job_id=ctx.job_id)
        cid = c.id
    ctx.log(f"场景图 {name}：候选 {cid}")
    return {"candidate_ids": [cid], "file": sv.media.rel(dst)}
