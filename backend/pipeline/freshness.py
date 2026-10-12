"""镜头首帧 / 视频候选「还是不是最新」：按**候选自己的引擎**重算期望的输入哈希再比。

多引擎协作是常态：同一镜头的候选可以来自 H3 图像、Gemini 网页……换引擎本身不让任何候选过期，
也不让批量运行重出、覆盖已选。不同引擎的参考图上限不同（zimage 0 张、H3 9 张、Gemini 网页 3 张），
所以期望的参考图打包也按候选那个引擎的上限截断——只因容量不同造成的差异不算过期；
参考图资产本身换了（定妆照换了一张）、提示词 / 画幅 / 时长改了，照样判过期。

参考图直出（Shot.video_mode = refs，见 domain.shots.refs_mode）的视频没有首帧：期望输入按出首帧那一套参考图
打包（shot_refs，按视频引擎的 max_refs 用 split_refs 截断）算，换了定妆照 / 场景图 / 道具图就过期。

views.shot_detail（界面过期标记和原因）、keyframes.run_keyframe / videos.run_video（批量运行跳过、
新候选抢不抢选定）共用这里，避免几处判断走偏。已存的 input_hash 不变。"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from backend.domain.refs import Ref, legacy_video_refs, shot_refs, split_refs, without_props
from backend.domain.shots import refs_mode
from backend.pipeline.continuity import VideoRefPlan, video_ref_plan
from backend.pipeline.prompting import (keyframe_input_hash, keyframe_inputs, video_input_hash,
                                        video_inputs, video_refs_input_hash, video_refs_inputs)


@dataclass
class Expect:
    """按某个引擎现在出会得到的输入：hash 是新候选记的哈希，ok 是算「没过期」的哈希（含老规则），
    inputs 是同结构摘要（stale_reason 用）。"""
    engine: str
    hash: str
    ok: set[str]
    inputs: dict
    max_refs: int = 0
    used: list[Ref] = field(default_factory=list)
    plan: VideoRefPlan | None = None
    dropped: list[Ref] = field(default_factory=list)   # 超出上限没带上的参考图（参考图直出的日志用）
    refs_mode: bool = False                            # 参考图直出（没有首帧）


def _ids(refs) -> list[int]:
    return [r.candidate_id for r in refs]


def image_caps(engines, name: str):
    try:
        return engines.image(name).caps
    except KeyError:
        return None


def video_caps(engines, name: str):
    try:
        return engines.video(name).caps
    except KeyError:
        return None


def image_max_refs_fn(engines) -> Callable[[str], int]:
    def f(name: str) -> int:
        caps = image_caps(engines, name)
        return caps.max_refs if caps is not None else 0
    return f


# ------------------------------------------------------------ 首帧
def keyframe_expect(s, shot, proj, style, engine_name: str, max_refs: int,
                    packed: list[Ref] | None = None) -> Expect:
    """engine_name 出这一镜首帧时的期望输入。参考图按 max_refs 截断（split_refs，和 run_keyframe 一致；
    引擎不吃参考图时哈希里不带参考图）。"""
    if packed is None:
        packed = shot_refs(s, shot)
    used = split_refs(packed, max_refs)[0] if max_refs > 0 else []
    h = keyframe_input_hash(shot, proj, style, engine_name, _ids(used))
    ok = {h}
    if any(r.kind == "prop" for r in used):
        # 加道具图之前出的首帧（打包里没有道具图）：不因为后来加了道具图过期
        old_used = split_refs(without_props(packed), max_refs)[0]
        ok.add(keyframe_input_hash(shot, proj, style, engine_name, _ids(old_used)))
    return Expect(engine_name, h, ok,
                  keyframe_inputs(shot, proj, style, engine_name, used),
                  max_refs=max_refs, used=used)


def first_frame_check(s, engines, shot, proj, style, cand, current: Expect,
                      packed: list[Ref] | None = None) -> tuple[bool, Expect]:
    """(选定首帧 cand 是否最新, 用来说过期原因的期望摘要)。current：按镜头当前引擎算的期望。
    先按当前引擎比；不对再按 cand 自己的引擎（上限按那个引擎的能力；引擎已不在配置里时用当前引擎的上限）。"""
    if cand.input_hash in current.ok:
        return True, current
    if not cand.engine or cand.engine == current.engine:
        return False, current
    caps = image_caps(engines, cand.engine)
    cap = caps.max_refs if caps is not None else current.max_refs
    own = keyframe_expect(s, shot, proj, style, cand.engine, cap, packed)
    return cand.input_hash in own.ok, own


# ------------------------------------------------------------ 视频
def video_expect(s, engines, shot, proj, style, ff_sel, engine_name: str, has_audio: bool,
                 max_refs: int, *, v_pack: list[Ref] | None = None) -> Expect:
    """选定首帧 ff_sel、视频引擎 engine_name 下的期望输入（和 run_video 同一套规则）。
    ok 里含以前规则的哈希：一律带参考图的老打包、接上一镜以前一律不带 / 不看画面里有没有写这个人。"""
    image_cap = image_max_refs_fn(engines)
    if v_pack is None:
        v_pack = legacy_video_refs(s, shot)
    ffid = ff_sel.id
    plan = video_ref_plan(s, shot, ff_sel, image_cap)
    old_plan = video_ref_plan(s, shot, ff_sel, image_cap, packed=v_pack)
    used = plan.refs[:max_refs]

    def vh(refs) -> str:
        return video_input_hash(shot, proj, style, ffid, engine_name, has_audio, _ids(refs))

    h = vh(used)
    ok = {h, vh(v_pack[:max_refs]), vh(old_plan.refs[:max_refs])}
    if plan.continued:
        ok.add(vh([]))
        prev_rule = video_ref_plan(s, shot, ff_sel, image_cap, in_frame_only=False)
        ok.add(vh(prev_rule.refs[:max_refs]))
    return Expect(engine_name, h, ok,
                  video_inputs(shot, proj, style, ffid, engine_name, has_audio, used),
                  max_refs=max_refs, used=used, plan=plan)


def video_refs_expect(s, shot, proj, style, engine_name: str, has_audio: bool, max_refs: int,
                      packed: list[Ref] | None = None) -> Expect:
    """参考图直出：engine_name 出这一镜视频时的期望输入。参考图按出首帧的同一套打包（shot_refs：场景图 →
    构图参考 → 定妆照 / 三视图 → 道具图），超出 max_refs 时 split_refs 先保住每个角色一张定妆照。"""
    if packed is None:
        packed = shot_refs(s, shot)
    used, dropped = split_refs(packed, max_refs) if max_refs > 0 else ([], list(packed))
    h = video_refs_input_hash(shot, proj, style, engine_name, has_audio, _ids(used))
    return Expect(engine_name, h, {h},
                  video_refs_inputs(shot, proj, style, engine_name, has_audio, used),
                  max_refs=max_refs, used=used, dropped=dropped, refs_mode=True)


def shot_video_expect(s, engines, shot, proj, style, ff_sel, engine_name: str, caps, *,
                      v_pack: list[Ref] | None = None,
                      packed: list[Ref] | None = None) -> Expect | None:
    """按镜头的生成方式算期望：参考图直出不要首帧；首帧模式没有选定首帧时为 None。caps 为 None（引擎不在
    配置里）时也为 None。"""
    if caps is None:
        return None
    if refs_mode(shot):
        return video_refs_expect(s, shot, proj, style, engine_name, caps.has_audio, caps.max_refs,
                                 packed)
    if ff_sel is None:
        return None
    # 首帧模式不带参考图的引擎（caps.frame_refs=False，Gemini / Muse 网页）：上限按 0 算，和它实际发的一致
    cap = caps.max_refs if getattr(caps, "frame_refs", True) else 0
    return video_expect(s, engines, shot, proj, style, ff_sel, engine_name, caps.has_audio,
                        cap, v_pack=v_pack)


def video_check(s, engines, shot, proj, style, ff_sel, cand,
                current: Expect | None) -> tuple[bool, Expect | None]:
    """(选定视频 cand 是否最新, 说原因用的期望摘要)。current 为 None：镜头当前的视频引擎不在配置里。
    两边的引擎都不认识时没法判断，按没过期算（和以前一样）。参考图直出时 ff_sel 可以是 None。"""
    if current is not None and cand.input_hash in current.ok:
        return True, current
    if cand.engine and (current is None or cand.engine != current.engine):
        caps = video_caps(engines, cand.engine)
        own = shot_video_expect(s, engines, shot, proj, style, ff_sel, cand.engine, caps)
        if own is not None:
            return cand.input_hash in own.ok, own
    if current is None:
        return True, None
    return False, current
