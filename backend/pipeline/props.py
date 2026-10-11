"""道具图（image.prop）：给分镜里出现的道具出一张「只拍道具、白底」的参考图。镜头里带了这个道具时，
首帧参考图打包把它排在出场角色后面（domain/refs.py），同一个道具在各镜里长得一样。

提示词和场景图一样只写要什么（正面写法）：默认的 zimage / krea2 是 cfg 1，负面提示词不起作用，
「no people」这类否定词反而会把人招进画面。风格只取风格锚里的成像词（去掉写人物的分句）。
"""
from __future__ import annotations

import re
import shutil

from backend.domain.hashing import input_hash
from backend.domain.models import Prop
from backend.domain.props import add_prop_image, prop_spec
from backend.domain.settings import prop_engine
from backend.engines.base import ImageRequest
from backend.pipeline.characters import seed_label
from backend.pipeline.keyframes import run_comfy
from backend.pipeline.locations import location_style
from backend.pipeline.prompting import join_negative, shot_seed
from backend.queue.errors import JobError
from backend.queue.handlers import HANDLERS

TASK = "image.prop"
PROP_ASPECT = "1:1"
# 支持负面提示词的引擎才用得上；cfg 1 的引擎构图全靠正面描述
PROP_NEGATIVE = "people, person, hands, fingers, text, watermark, clutter, busy background"
_CJK = re.compile(r"[⺀-鿿가-힯豈-﫿＀-￯]")


def _english(text: str) -> str:
    t = " ".join((text or "").split()).strip()
    return t if t and not _CJK.search(t) else ""


def prop_prompt(prop, anchor: str) -> str:
    """「<道具说法>, shown on its own as an object reference: …white background…」+ 补充描述 + 风格。"""
    name = _english(prop.name)
    extra = _english(prop.visual_prompt)
    if not name and not extra:
        raise JobError(f"道具 {prop.name} 还没有英文说法，先在「设定」的道具里写一句英文描述"
                       "（如 a dented red thermos with a chipped lid）")
    subject = (name or extra).rstrip(" .")
    subject = subject[:1].upper() + subject[1:]
    parts = [f"{subject}, shown on its own as an object reference: a single object, centered, the "
             "whole object in frame, on a plain white seamless background, soft even studio light, "
             "clean sharp detail."]
    if name and extra:
        parts.append(extra if extra.endswith((".", "!", "?")) else extra + ".")
    style = location_style(anchor)
    if style:
        parts.append(f"Style: {style}.")
    return " ".join(parts)


def _load_prop(s, prop_id: int) -> Prop:
    p = s.get(Prop, prop_id)
    if p is None:
        raise JobError(f"找不到道具 {prop_id}")
    return p


@HANDLERS.register(TASK, gpu=True)
def run_prop_image(ctx, payload: dict) -> dict:
    """出一张道具图候选。engine 不给：设置页道具图这一步 → 项目的图像引擎。"""
    from backend.domain.models import Project

    sv = ctx.services
    prop_id = int(payload["prop_id"])
    with sv.db.session() as s:
        prop = _load_prop(s, prop_id)
        proj = s.get(Project, prop.project_id)
        style = proj.style_bible
        engine_name = prop_engine(sv, proj.image_engine, payload.get("engine"))
        try:
            engine = sv.engines.image(engine_name)
        except KeyError as e:
            raise JobError(str(e)) from None
        prompt = prop_prompt(prop, style.anchor_text if style else "")
        negative = join_negative(proj.negative, style.negative if style else "", PROP_NEGATIVE)
        seed = shot_seed(proj.seed, prop_id + 70_000, ctx.job_id)
        slug, name = proj.slug, prop.name
        spec = prop_spec(prop)
    call = engine.build(ImageRequest(prompt=prompt, negative=negative, aspect=PROP_ASPECT, seed=seed,
                                     prefix=f"studio/{slug}/prop_{prop_id}"))
    ctx.log(f"道具图 {name} · {engine_name} · {seed_label(call, seed)}")
    files, run_dir = run_comfy(ctx, call, slug)
    dst = sv.media.new_media_path(slug, "prop", files[0].suffix)
    shutil.copyfile(files[0], dst)
    with sv.db.session() as s:
        c = add_prop_image(s, prop_id, file_path=sv.media.rel(dst), engine=engine_name,
                           params={**call.meta, "prompt": prompt, "spec": spec,
                                   "workflow": sv.media.rel(run_dir / "workflow.api.json")},
                           input_hash=input_hash("prop_image", prompt, negative, engine_name,
                                                 PROP_ASPECT, seed),
                           job_id=ctx.job_id)
        cid = c.id
    ctx.log(f"道具图 {name}：候选 {cid}")
    return {"candidate_ids": [cid], "file": sv.media.rel(dst)}
