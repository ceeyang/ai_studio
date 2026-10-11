"""dialogue.fit_khmer：按高棉语配音的时长改写中文台词，让 H3 重出视频时嘴型和说话节奏落在配音的位置上。

步骤：①缺高棉语的句子先翻译（同 subtitles.translate）；②Edge TTS 按角色音色量每句高棉语多长（剪掉首尾静音）；
③LLM 改写中文，用普通话 Edge TTS 实测，±fit_tolerance 以内算合格，最多 fit_rounds 轮，取最接近的一版；
④中文换成改写版（原句存 zh_original），每句窗口改成高棉语时长，镜头不够长就加长（不超过引擎上限），
音频提示里写死的旧台词换掉、附上每句起止。之后视频输入哈希变了，videos 阶段会重出这些镜头。

每句台词新增：zh_original、km_seconds、zh_seconds、fit_ratio（= zh_seconds / km_seconds）、fit_rounds。
"""
from __future__ import annotations

import re

from backend.domain.models import Location, SceneBlock, Shot
from backend.llm.schemas import FitOut
from backend.pipeline.common import continue_chain, load_episode
from backend.pipeline.post import KHMER, _tts, speakers_without_voice, translate_lines
from backend.post.fit import FitLine, fit_lines, retime_shot, update_audio_prompt, within
from backend.queue.errors import JobError
from backend.queue.handlers import HANDLERS

FIT_TASK = "dialogue.fit_khmer"
HAN = re.compile(r"[㐀-鿿]")


def _scene_of(s, sh) -> str:
    """这一镜所在的场景（中文）：「老街修鞋摊（黄昏，安静、怀旧）」，给改写台词的模型当语境。"""
    blk = s.get(SceneBlock, sh.scene_block_id) if sh.scene_block_id else None
    loc = s.get(Location, blk.location_id) if blk is not None and blk.location_id else None
    if loc is None:
        return ""
    extra = "，".join(x for x in (loc.time_of_day, loc.atmosphere) if x)
    return f"{loc.name}（{extra}）" if extra else loc.name


def _load(sv, episode_id: int, wanted: set[int]):
    with sv.db.session() as s:
        ep, proj = load_episode(s, episode_id)
        ids = {sh.id for sh in ep.shots}
        unknown = sorted(wanted - ids)
        if unknown:
            raise JobError(f"这些镜头不在本集：{', '.join(map(str, unknown))}")
        shots = []
        for sh in ep.shots:
            if wanted and sh.id not in wanted:
                continue
            if not any((d.get("zh") or "").strip() for d in sh.dialogue or []):
                continue
            engine_name = sh.video_engine or proj.video_engine
            try:
                max_seconds = sv.engines.video(engine_name).caps.max_seconds
            except KeyError as e:
                raise JobError(str(e)) from None
            shots.append({"id": sh.id, "code": sh.code, "duration": float(sh.duration),
                          "dialogue": [dict(d) for d in sh.dialogue], "audio": sh.audio_prompt or "",
                          "max_seconds": float(max_seconds), "scene": _scene_of(s, sh)})
        return proj.slug, proj.title, {c.name: c.voice for c in proj.characters}, shots


def _translate_missing(ctx, slug, title, shots, payload, episode_id) -> None:
    todo = []
    for sh in shots:
        for j, d in enumerate(sh["dialogue"]):
            zh = (d.get("zh_original") or d.get("zh") or "").strip()
            if zh and not (d.get("km") or "").strip():
                todo.append({"id": f"{sh['code']}.{j + 1}", "speaker": d.get("speaker") or "",
                             "zh": zh, "seconds": max(float(d.get("end") or 0)
                                                      - float(d.get("start") or 0), 0.5),
                             "ref": (sh, j)})
    if not todo:
        return
    ctx.log(f"先翻译 {len(todo)} 句缺高棉语的台词")
    km = translate_lines(ctx, slug=slug, title=title,
                         todo=[{k: v for k, v in t.items() if k != "ref"} for t in todo],
                         glossary=payload.get("glossary") or {}, manual=payload.get("manual") or {},
                         key=f"{FIT_TASK}:{episode_id}:translate")
    for t in todo:
        sh, j = t["ref"]
        sh["dialogue"][j].update(km=km[t["id"]], km_review="ai_draft")


def _rewriter(ctx, slug: str, title: str, tol: float, payload: dict, episode_id: int,
              scenes: dict[str, str] | None = None):
    sv = ctx.services

    def rewrite(rnd: int, todo: list[dict]) -> dict[str, str]:
        lines = [{**t, "scene": (scenes or {}).get(t["id"].split(".")[0], "")} for t in todo]
        system, user = sv.prompts(slug).render("dialogue_fit_khmer", title=title, round=rnd,
                                               tol=tol, lines=lines)
        want = [t["id"] for t in todo]

        def check(out: FitOut) -> None:
            got = [x.id for x in out.lines]
            problems = []
            miss = [i for i in want if i not in got]
            extra = [i for i in got if i not in want]
            if miss:
                problems.append(f"缺少这些 id：{', '.join(miss)}")
            if extra:
                problems.append(f"多出或写错的 id：{', '.join(extra)}")
            bad = [x.id for x in out.lines if not HAN.search(x.zh) or KHMER.search(x.zh)]
            if bad:
                problems.append(f"这些不是中文台词：{', '.join(bad)}")
            if problems:
                raise ValueError("；".join(problems))

        out = sv.llm.generate(FIT_TASK, system, user, FitOut, check=check, log=ctx.log,
                              manual=payload.get("manual") or {},
                              key=f"{FIT_TASK}:{episode_id}:r{rnd}")
        return {x.id: x.zh.strip() for x in out.lines}
    return rewrite


@HANDLERS.register(FIT_TASK)
def run_fit_khmer(ctx, payload: dict) -> dict:
    sv = ctx.services
    cfg = sv.cfg.dub
    episode_id = int(payload["episode_id"])
    force = bool(payload.get("force"))
    wanted = {int(x) for x in payload.get("shot_ids") or []}
    slug, title, voices, shots = _load(sv, episode_id, wanted)
    if not shots:
        ctx.log("没有带中文台词的镜头")
        continue_chain(ctx, episode_id, payload)
        return {"lines": 0, "shots": []}
    nov = speakers_without_voice([sh["dialogue"] for sh in shots], voices)
    if nov:
        raise JobError(f"角色 {', '.join(nov)} 没有设置配音音色（如 km-KH-PisethNeural），"
                       "量不了高棉语时长")
    _translate_missing(ctx, slug, title, shots, payload, episode_id)

    cache = sv.media.project_dir(slug) / "media" / "dub"
    cache.mkdir(parents=True, exist_ok=True)
    tol = float(cfg.fit_tolerance)
    lines: list[FitLine] = []
    where: dict[str, tuple[dict, int]] = {}
    kept = 0
    total = sum(1 for sh in shots for d in sh["dialogue"] if (d.get("zh") or "").strip())
    for sh in shots:
        for j, d in enumerate(sh["dialogue"]):
            if not (d.get("zh") or "").strip():
                continue
            ctx.check_canceled()
            voice = voices[d.get("speaker") or ""]
            _, km_s = _tts(sv, cache, d["km"].strip(), voice, 0)
            d["km_seconds"] = round(km_s, 3)
            ctx.progress(len(where) + kept + 1, total)
            zh_voice = cfg.zh_voices.get(voice) or cfg.zh_voice_default
            if not force and d.get("zh_original") and km_s > 0:
                # 上次改过：当前中文（可能又被手改过）重新量一下，仍然合格就不再改
                _, zh_s = _tts(sv, cache, d["zh"].strip(), zh_voice, 0)
                if within(zh_s / km_s, tol):
                    d.update(zh_seconds=round(zh_s, 3), fit_ratio=round(zh_s / km_s, 3))
                    kept += 1
                    continue
            ln = FitLine(id=f"{sh['code']}.{j + 1}", speaker=d.get("speaker") or "",
                         zh_original=(d.get("zh_original") or d["zh"]).strip(),
                         km=d["km"].strip(), km_seconds=km_s,
                         zh_voice=zh_voice)
            lines.append(ln)
            where[ln.id] = (sh, j)
    if kept:
        ctx.log(f"{kept} 句上次已改好、现在量仍合格，跳过（要重改用 force）")

    def measure(text: str, voice: str) -> float:
        ctx.check_canceled()
        return _tts(sv, cache, text, voice, 0)[1]

    if lines:
        scenes = {sh["code"]: sh.get("scene", "") for sh in shots}
        fit_lines(lines, measure, _rewriter(ctx, slug, title, tol, payload, episode_id, scenes),
                  tol=tol, rounds=int(cfg.fit_rounds), log=ctx.log)

    report, changed = [], []
    for ln in lines:
        sh, j = where[ln.id]
        d = sh["dialogue"][j]
        before = d.get("zh") or ""
        sh.setdefault("swaps", []).extend([(before, ln.zh), (ln.zh_original, ln.zh)])
        d.update(zh_original=ln.zh_original, zh=ln.zh, km_seconds=round(ln.km_seconds, 3),
                 zh_seconds=round(ln.zh_seconds, 3), fit_ratio=round(ln.ratio, 3),
                 fit_rounds=ln.rounds)
        report.append({"id": ln.id, "speaker": ln.speaker, "zh_original": ln.zh_original,
                       "zh": ln.zh, "km": ln.km, "km_seconds": round(ln.km_seconds, 2),
                       "zh_original_seconds": ln.tries[0]["seconds"],
                       "zh_seconds": round(ln.zh_seconds, 2), "ratio": round(ln.ratio, 3),
                       "rounds": ln.rounds, "ok": within(ln.ratio, tol)})
    for sh in shots:
        dlg, dur, over = retime_shot(sh["dialogue"], sh["duration"], sh["max_seconds"])
        audio = update_audio_prompt(sh["audio"], sh.get("swaps", []),
                                    [d for d in dlg if (d.get("zh") or "").strip()])
        changed.append({"code": sh["code"], "duration": sh["duration"], "new_duration": dur,
                        "over": over})
        sh.update(dialogue=dlg, new_duration=dur, new_audio=audio)
    with sv.db.session() as s:
        for sh in shots:
            row = s.get(Shot, sh["id"])
            if row is None:
                continue
            row.dialogue = sh["dialogue"]
            row.duration = sh["new_duration"]
            row.audio_prompt = sh["new_audio"]
    # 日志等写事务提交后再记（ctx.log 另开连接写 jobs 表）
    for r in report:
        ctx.log(f"{r['id']} {r['speaker']}：「{r['zh_original']}」{r['zh_original_seconds']:.2f}s → "
                f"「{r['zh']}」{r['zh_seconds']:.2f}s ｜ 高棉语 {r['km_seconds']:.2f}s ｜ "
                f"比 {r['ratio']:.2f}{'' if r['ok'] else '（超出 ±' + format(tol, '.0%') + '）'}"
                f"（第 {r['rounds']} 轮）")
    for c in changed:
        if c["new_duration"] != c["duration"] or c["over"]:
            ctx.log(f"{c['code']} 时长 {c['duration']:g}s → {c['new_duration']:g}s"
                    + ("（到引擎上限仍放不下，台词会压到结尾）" if c["over"] else ""))
    continue_chain(ctx, episode_id, payload)
    return {"lines": len(report), "kept": kept, "ok": sum(1 for r in report if r["ok"]),
            "report": report, "shots": changed}
