"""首帧阶段：每个任务给一个镜头出一张首帧候选。"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from backend.domain.refs import shot_refs, split_refs
from backend.domain.assets import ASSET_ENGINE
from backend.domain.models import Shot
from backend.domain.shots import refs_mode
from backend.domain.slots import add_candidate, get_or_create_slot, selected_candidate
from backend.engines.base import ImageRequest
from backend.engines.runninghub.client import RunningHubError
from backend.pipeline.common import load_episode
from backend.pipeline.freshness import first_frame_check, image_caps, keyframe_expect
from backend.pipeline.prompting import (engine_image_prompt, join_negative, only_people_clause,
                                        shot_image_prompt, shot_seed)
from backend.queue.errors import JobCanceled, JobError
from backend.queue.handlers import HANDLERS
from backend.queue.trace import trace_add, trace_rel, trace_update

# 参考图的角色（Ref.kind → 调用明细里显示的名字）
REF_ROLE = {"location": "场景图", "shot_ref": "构图参考", "portrait": "定妆照",
            "turnaround": "三视图", "prop": "道具图", "first_frame": "首帧", "end_frame": "尾帧"}
PARAM_KEYS = ("size", "seconds", "frames", "fps", "seed", "steps", "turbo", "model", "service",
              "site", "nondeterministic")


def load_shot(s, shot_id: int):
    shot = s.get(Shot, shot_id)
    if shot is None:
        raise JobError(f"找不到镜头 {shot_id}")
    ep, proj = load_episode(s, shot.episode_id)
    return shot, ep, proj, proj.style_bible


def ref_roles(packed, paths) -> dict[str, str]:
    """参考图路径 → 角色（场景图 / 构图参考 / 定妆照 / 三视图），记调用明细用。"""
    return {str(p): REF_ROLE.get(r.kind, r.kind or "") for r, p in zip(packed, paths)}


def engine_trace(call, req=None, labels: dict[str, str] | None = None,
                 roles: dict[str, str] | None = None, what: str = "") -> tuple[str, dict]:
    """一次引擎提交的调用明细：发给引擎的最终提示词、负面词、参考图（按顺序、标明角色）、参数、workflow。"""
    labels, roles = labels or {}, roles or {}
    meta = dict(call.meta or {})
    wf = call.workflow if isinstance(call.workflow, dict) else {}
    runner = getattr(call, "runner", "comfyui")
    source = getattr(req, "prompt", None) or meta.get("prompt") or ""
    final = wf.get("prompt") if runner in ("browser", "api") and isinstance(wf.get("prompt"), str) \
        else source
    try:
        wf_text = json.dumps(wf, ensure_ascii=False)
    except (TypeError, ValueError):
        wf_text = str(wf)
    negative = getattr(req, "negative", None) or meta.get("negative_ignored") or ""
    dropped = set(call.dropped_refs or [])
    refs: list[dict] = []
    first = getattr(req, "first_frame", None)
    if first:
        refs.append({"path": trace_rel(first), "label": "首帧", "role": "首帧", "sent": True})
    for p in list(getattr(req, "refs", None) or []):
        key = str(p)
        refs.append({"path": trace_rel(p), "label": labels.get(key) or Path(key).name,
                     "role": roles.get(key, ""), "sent": key not in dropped})
    end = getattr(req, "end_frame", None)
    if end:
        refs.append({"path": trace_rel(end), "label": "尾帧", "role": "尾帧",
                     "sent": "end_frame" in wf_text or str(end) in wf_text})
    params = {k: meta[k] for k in PARAM_KEYS if k in meta and meta[k] is not None}
    if req is not None:
        params["aspect"] = getattr(req, "aspect", None)
        if getattr(req, "seconds", None) is not None and "seconds" not in params:
            params["seconds"] = req.seconds
        if getattr(req, "min_seconds", None):
            params["min_seconds"] = req.min_seconds
        if "seed" not in params and not meta.get("nondeterministic"):
            params["seed"] = getattr(req, "seed", None)
    data = {"engine": call.engine, "runner": runner, "output_kind": call.output_kind,
            "family": call.model_family, "what": what, "status": "running",
            "prompt": final, "negative": negative,
            "negative_sent": bool(negative) and "negative_ignored" not in meta and negative in wf_text,
            "refs": refs, "params": params, "workflow": wf}
    if source and source != final:
        data["source_prompt"] = source
    kind = "视频" if call.output_kind == "video" else "出图"
    title = " · ".join(x for x in (kind, call.engine, what) if x)
    return title, data


def run_comfy(ctx, call, slug: str, ref_labels: dict[str, str] | None = None, *,
              req=None, what: str = "", ref_kinds: dict[str, str] | None = None):
    """执行引擎调用：ComfyUI 工作流交给 ComfyRunner，网页出图（call.runner == "browser"）交给
    BrowserRunner，RunningHub 云端（call.runner == "runninghub"）交给 RunningHubRunner，
    官方 API（call.runner == "api"）交给 ApiRunner。
    都把产物下载到任务目录，返回文件列表和任务目录。
    req（ImageRequest / VideoRequest）、what（「首帧 S03」）、ref_kinds 只用来记任务的调用明细。"""
    title, data = engine_trace(call, req, ref_labels, ref_kinds, what)
    seq = trace_add("engine", title, data)
    t0 = time.monotonic()
    try:
        files, run_dir = _run_engine(ctx, call, slug, ref_labels)
    except BaseException as e:
        trace_update(seq, {"status": "canceled" if isinstance(e, JobCanceled) else "error",
                           "error": str(e)[:3000], "seconds": round(time.monotonic() - t0, 1)})
        raise
    wf_file = run_dir / "workflow.api.json"
    trace_update(seq, {"status": "ok", "seconds": round(time.monotonic() - t0, 1),
                       "outputs": [trace_rel(f) for f in files],
                       "workflow_file": trace_rel(wf_file) if wf_file.exists() else None})
    return files, run_dir


def _run_engine(ctx, call, slug: str, ref_labels: dict[str, str] | None = None):
    sv = ctx.services
    kind = getattr(call, "runner", "comfyui")
    browser = kind == "browser"
    if browser and getattr(sv, "browser", None) is None:
        raise JobError("网页出图的浏览器没有配置")
    if kind == "runninghub" and getattr(sv, "runninghub", None) is None:
        raise JobError("RunningHub 没有配置（config.yaml 的 runninghub）")
    if kind == "api" and getattr(sv, "api", None) is None:
        raise JobError("官方 API 引擎没有初始化")
    if kind == "comfyui" and sv.comfy is None:
        raise JobError("ComfyUI 未配置")
    labels = ref_labels or {}
    for ref in call.dropped_refs:
        ctx.log(f"超出引擎参考图上限，已丢弃：{labels.get(ref, ref)}")
    run_dir = sv.media.run_dir(slug, ctx.job_id)
    if kind == "api":
        from backend.engines.api.runner import ApiError
        try:
            files = sv.api.run(call, run_dir=run_dir, cancel=ctx.cancel_event,
                               on_progress=ctx.progress, log=ctx.log)
        except ApiError as e:
            raise JobError(str(e)) from None
    elif kind == "runninghub":
        try:
            files = sv.runninghub.run(call, run_dir=run_dir, cancel=ctx.cancel_event,
                                      on_progress=ctx.progress, log=ctx.log)
        except RunningHubError as e:
            raise JobError(str(e)) from None
    elif browser:
        files = sv.browser.run(call, run_dir=run_dir, cancel=ctx.cancel_event,
                               on_progress=ctx.progress, log=ctx.log)
    else:
        files = sv.comfy.run(call, run_dir=run_dir, cancel=ctx.cancel_event,
                             on_progress=ctx.progress)
    return files, run_dir


def missing_portraits(s, shot) -> list[str]:
    """这一镜出场、但还没有选定定妆照的角色名。"""
    from backend.domain.characters import portrait_of
    from backend.domain.models import Character

    out = []
    for c in shot.cast or []:
        cid = c.get("character_id") if isinstance(c, dict) else None
        ch = s.get(Character, cid) if cid is not None else None
        if ch is not None and not ch.voice_only and ch.name not in out                 and portrait_of(s, ch.id) is None:
            out.append(ch.name)
    return out


def ref_labels(packed, paths) -> dict[str, str]:
    """引擎丢弃参考图时报的是路径，换成"陈师傅 三视图·背面"这样的名字记日志。"""
    return {str(p): r.label for r, p in zip(packed, paths)}


def _first_frame_fresh(s, engines, shot_id: int, cur) -> bool:
    """选定首帧是否还是最新（新候选抢不抢选定用）：按它自己的引擎判断；从已有图片里选的算最新。"""
    if cur.engine == ASSET_ENGINE:
        return True
    shot, _, proj, style = load_shot(s, shot_id)
    if shot.continue_from_prev:
        return False
    engine_name = shot.image_engine or proj.image_engine
    caps = image_caps(engines, engine_name)
    exp = keyframe_expect(s, shot, proj, style, engine_name, caps.max_refs if caps else 0)
    return first_frame_check(s, engines, shot, proj, style, cur, exp)[0]


@HANDLERS.register("image.keyframe", gpu=True)
def run_keyframe(ctx, payload: dict) -> dict:
    sv = ctx.services
    shot_id = int(payload["shot_id"])
    with sv.db.session() as s:
        shot, ep, proj, style = load_shot(s, shot_id)
        continuing = bool(shot.continue_from_prev)
        direct, code = refs_mode(shot), shot.code
    if continuing:   # 接上一镜：不出图，首帧取上一镜视频的最后一帧（选了参考图直出也按首帧模式）
        from backend.pipeline.continuity import run_continue_frame
        return run_continue_frame(ctx, payload)
    if direct:   # 参考图直出：视频直接用参考图出，不要首帧
        ctx.log(f"首帧 {code}：这一镜参考图直出，不出首帧")
        return {"skipped": True, "candidate_ids": [], "reason": "refs_mode"}
    with sv.db.session() as s:
        shot, ep, proj, style = load_shot(s, shot_id)
        engine_name = shot.image_engine or proj.image_engine
        try:
            engine = sv.engines.image(engine_name)
        except KeyError as e:
            raise JobError(str(e)) from None
        anchor = style.anchor_text if style else ""
        notes: list[str] = []
        # 发给引擎：景别那一句放在风格锚前面（只改发出去的版本，输入哈希仍按锚在前算）
        prompt = engine_image_prompt(shot_image_prompt(shot, proj, notes), anchor)
        # 有只出声角色的台词：写明画面里只有谁（只改发出去的版本，不进哈希）
        only = only_people_clause(shot, proj)
        if only:
            prompt = f"{prompt} {only}"
        negative = join_negative(proj.negative, style.negative if style else "")
        packed = shot_refs(s, shot)
        no_portrait = missing_portraits(s, shot) if engine.caps.max_refs > 0 else []
        # 超出上限时先保住每个角色的定妆照（split_refs），只把留下的发给引擎；引擎不吃参考图时照发，
        # 由下面的日志说明用不上
        used, dropped = (split_refs(packed, engine.caps.max_refs) if engine.caps.max_refs > 0
                         else (packed, []))
        refs = [sv.media.abs(r.file_path) for r in used]
        labels = ref_labels(used, refs)
        kinds = ref_roles(used, refs)
        # 输入哈希 / 摘要和界面过期判断同一套（pipeline.freshness）：引擎不吃参考图时不带参考图
        exp = keyframe_expect(s, shot, proj, style, engine_name, engine.caps.max_refs, packed)
        h, inputs = exp.hash, exp.inputs
        # 只要一个候选、又不强制时，输入没变就不重出（多候选是用户明确要抽卡）。
        # 「没变」按选定候选自己的引擎判断：换了镜头 / 项目的引擎不重出已选的（多引擎协作）
        if not payload.get("force") and int(payload.get("candidates") or 1) == 1:
            cur = selected_candidate(s, "shot", shot.id, "first_frame")
            if cur is not None and first_frame_check(s, sv.engines, shot, proj, style, cur, exp,
                                                     packed)[0]:
                ctx.log(f"首帧 {shot.code}：输入没变，跳过（要重出用 --force）")
                return {"skipped": True, "candidate_ids": []}
            if cur is not None and cur.engine == ASSET_ENGINE:
                ctx.log(f"首帧 {shot.code}：选定的首帧是从已有图片里选的，跳过（要重出用 --force）")
                return {"skipped": True, "candidate_ids": []}
        slot = get_or_create_slot(s, "shot", shot.id, "first_frame")
        seed = shot_seed(proj.seed, shot.id, len(slot.candidates))
        slug, code, aspect = proj.slug, shot.code, proj.aspect
    if not shot.image_prompt.strip():
        raise JobError(f"镜头 {code} 没有 image_prompt")
    req = ImageRequest(prompt=prompt, negative=negative, aspect=aspect, seed=seed,
                       refs=refs, prefix=f"studio/{slug}/kf_{code}")
    call = engine.build(req)
    seed_text = ("无种子（网页出图每次不同）" if call.runner == "browser"
                 else "无种子（API 出图每次不同）" if call.runner == "api" else f"seed {seed}")
    ctx.log(f"首帧 {code} · {engine_name} · {seed_text} · 参考图 {len(refs)} 张"
            f"{'：' + '、'.join(labels.values()) if labels else ''}")
    for n in notes:
        ctx.log(f"首帧 {code}：{n}")
    for r in dropped:
        ctx.log(f"超出引擎参考图上限，已丢弃：{r.label}")
    lost = [r.label for r in dropped if r.kind == "portrait"]
    if lost:
        ctx.log(f"首帧 {code}：{engine_name} 最多 {engine.caps.max_refs} 张参考图，装不下所有出场角色的"
                f"定妆照，{'、'.join(lost)}没带上，这几个人的脸只能按文字出；要锁脸就减少出场角色或换"
                "参考图上限更大的引擎")
    if no_portrait:
        ctx.log(f"首帧 {code}：{'、'.join(no_portrait)} 还没有定妆照，{engine_name} 只能按文字出脸；"
                "补了定妆照之后这张首帧会过期重出")
    if refs and engine.caps.max_refs == 0:
        ctx.log(f"首帧 {code}：{engine_name} 不吃参考图，{'、'.join(labels.values())}用不上，"
                f"人物一致只靠提示词里的外貌文字；要锁脸可换 H3 图像")
    files, run_dir = run_comfy(ctx, call, slug, labels, req=req, what=f"首帧 {code}",
                               ref_kinds=kinds)
    dst = sv.media.new_media_path(slug, "first_frame", files[0].suffix)
    shutil.copyfile(files[0], dst)
    with sv.db.session() as s:
        slot = get_or_create_slot(s, "shot", shot_id, "first_frame")
        c = add_candidate(s, slot, file_path=sv.media.rel(dst), engine=engine_name,
                          params={**call.meta, "workflow": sv.media.rel(run_dir / "workflow.api.json"),
                                  "inputs": inputs},
                          input_hash=h, job_id=ctx.job_id,
                          is_fresh=lambda cur: _first_frame_fresh(s, sv.engines, shot_id, cur),
                          # 重拍一张：新的一条直接选中；一次出多张就不抢选定（不然最后完成的那张随机胜出），
                          # 让用户看「N 张新候选」自己挑
                          force_select=bool(payload.get("force"))
                          and int(payload.get("candidates") or 1) == 1)
        cid = c.id
    return {"candidate_ids": [cid], "file": sv.media.rel(dst)}
