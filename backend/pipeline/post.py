"""后期任务：字幕翻译（subtitles.translate）、高棉语配音（post.dub），以及导出共用的"成片剪辑表"。

导出（export.episode）带选项时的任务链：
  [subtitles.translate（有台词缺高棉语时）] → [post.dub（dub=km 时）] → export.episode
post.dub 只负责把每句台词用 Edge TTS 念出来并塞进窗口（要联网、慢、可单独重试，结果按文本+音色+语速缓存），
写一份清单；export.episode 合成画面后按清单把人声叠上去，再烧字幕、接片尾卡。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from backend.domain.hashing import input_hash
from backend.domain.models import Episode, Shot
from backend.domain.slots import selected_candidate
from backend.engines.ffmpeg import xfade_timeline
from backend.llm.schemas import TranslationOut
from backend.pipeline.common import load_episode
from backend.post.dub import edge_tts_save, fit_rate, plan_dub_lines
from backend.post.options import ExportOptions
from backend.post.timeline import trim_limit
from backend.queue.errors import JobError
from backend.queue.handlers import HANDLERS

OUTPUT_FPS = 24
KHMER = re.compile("[ក-៿]")
TRANSLATE_TASK = "subtitles.translate"


# ---------------------------------------------------------------- 剪辑表
@dataclass
class Cut:
    """一集的成片素材：按镜头顺序的选定视频、台词、分镜时长，以及配音音色和片尾卡。"""
    episode_id: int
    slug: str
    number: int
    title: str
    aspect: str
    codes: list[str] = field(default_factory=list)
    clips: list[Path] = field(default_factory=list)
    clip_rels: list[str] = field(default_factory=list)
    transitions: list[str] = field(default_factory=list)   # 接缝处的转场，比镜头少一个
    dialogue: list[list[dict]] = field(default_factory=list)
    shot_seconds: list[float] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    voices: dict[str, str | None] = field(default_factory=dict)
    end_card: dict | None = None


def load_cut(sv, episode_id: int, *, allow_missing: bool = False) -> Cut:
    with sv.db.session() as s:
        ep, proj = load_episode(s, episode_id)
        if not ep.shots:
            raise JobError("这一集还没有镜头")
        cut = Cut(episode_id, proj.slug, ep.number, proj.title, proj.aspect,
                  voices={c.name: c.voice for c in proj.characters}, end_card=proj.end_card)
        trans = []
        for sh in ep.shots:
            c = selected_candidate(s, "shot", sh.id, "video")
            if c is None:
                cut.missing.append(sh.code)
                continue
            cut.codes.append(sh.code)
            cut.clips.append(sv.media.abs(c.file_path))
            cut.clip_rels.append(c.file_path)
            cut.dialogue.append([dict(d) for d in sh.dialogue or []])
            cut.shot_seconds.append(float(sh.duration))
            trans.append(sh.transition_out)
        cut.transitions = trans[:-1]
    if cut.missing and not allow_missing:
        raise JobError(f"以下镜头还没有选定视频：{', '.join(cut.missing)}")
    if not cut.clips:
        raise JobError("没有可合成的视频")
    return cut


def clip_limits(sv, cut: Cut, trim: bool) -> tuple[list[float], list[float | None]]:
    """每段视频的实际时长，和合成时最多用多少秒（None = 整段）。规则见 backend/post/timeline.py。"""
    secs = [sv.ffmpeg.video_duration(c) for c in cut.clips]
    limits: list[float | None] = []
    for clip, sec, dur, dlg in zip(cut.clips, secs, cut.shot_seconds, cut.dialogue):
        if not trim or trim_limit(sec, dur, dlg) is None:   # 按分镜就不该裁的，不用再量音轨
            limits.append(None)
            continue
        limits.append(trim_limit(sec, dur, dlg, audio_end=sv.ffmpeg.sound_end(clip)))
    return secs, limits


def planned_timeline(secs, limits, transitions) -> tuple[list[float], list[float]]:
    durs = [min(sec, lim) if lim else sec for sec, lim in zip(secs, limits)]
    _, starts, _ = xfade_timeline(durs, transitions, OUTPUT_FPS)
    return durs, starts


def lines_missing_km(cut_or_dialogue, codes: list[str] | None = None) -> list[str]:
    """有中文却没有高棉语的镜头编号。"""
    if isinstance(cut_or_dialogue, Cut):
        dialogue, codes = cut_or_dialogue.dialogue, cut_or_dialogue.codes
    else:
        dialogue = cut_or_dialogue
    return [code for code, dlg in zip(codes or [], dialogue)
            if any((d.get("zh") or "").strip() and not (d.get("km") or "").strip() for d in dlg)]


def speakers_without_voice(dialogue: list[list[dict]], voices: dict[str, str | None]) -> list[str]:
    names = {d.get("speaker") or "" for dlg in dialogue for d in dlg if (d.get("km") or d.get("zh"))}
    return sorted(n for n in names if not (voices.get(n) or "").strip())


def dub_key(cut: Cut, limits: list[float | None]) -> str:
    """配音清单对应的输入：换了视频、裁剪点、台词时间、译文或音色，清单就作废。"""
    lines = [[(d.get("speaker"), d.get("km"), d.get("start")) for d in dlg] for dlg in cut.dialogue]
    return input_hash("dub", cut.clip_rels, [round(x, 3) if x else None for x in limits], lines,
                      sorted((k, v) for k, v in cut.voices.items() if v))


def dub_manifest_path(sv, cut: Cut) -> Path:
    d = sv.media.project_dir(cut.slug) / "media" / "dub"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"ep{cut.number:02d}_km.json"


# ---------------------------------------------------------------- 字幕翻译
@HANDLERS.register(TRANSLATE_TASK)
def run_translate(ctx, payload: dict) -> dict:
    """给缺高棉语的台词出 AI 译稿（force 时全部重翻），写回 dialogue[].km，标记 km_review=ai_draft。"""
    sv = ctx.services
    episode_id = int(payload["episode_id"])
    force = bool(payload.get("force"))
    with sv.db.session() as s:
        ep, proj = load_episode(s, episode_id)
        slug, title = proj.slug, proj.title
        todo, skipped = [], 0
        for sh in ep.shots:
            dlg = sh.dialogue or []
            for j, d in enumerate(dlg):
                if not (d.get("zh") or "").strip():
                    continue
                if (d.get("km") or "").strip() and not force:
                    skipped += 1
                    continue
                nxt = float(dlg[j + 1].get("start") or 0) if j + 1 < len(dlg) else float(sh.duration)
                todo.append({"id": f"{sh.code}.{j + 1}", "shot_id": sh.id, "index": j,
                             "speaker": d.get("speaker") or "", "zh": d["zh"].strip(),
                             "seconds": max(nxt - float(d.get("start") or 0), 0.5)})
    if not todo:
        ctx.log("没有需要翻译的台词")
        return {"translated": 0, "skipped": skipped}
    system, user = sv.prompts(slug).render("subtitles_translate", title=title,
                                           glossary=payload.get("glossary") or {}, lines=todo)
    want = [t["id"] for t in todo]

    def check(out: TranslationOut) -> None:
        got = [x.id for x in out.lines]
        problems = []
        miss = [i for i in want if i not in got]
        extra = [i for i in got if i not in want]
        if miss:
            problems.append(f"缺少这些 id：{', '.join(miss)}")
        if extra:
            problems.append(f"多出或写错的 id：{', '.join(extra)}")
        bad = [x.id for x in out.lines if not KHMER.search(x.km)]
        if bad:
            problems.append(f"这些译文里没有高棉文字符：{', '.join(bad)}")
        if problems:
            raise ValueError("；".join(problems))

    ctx.log(f"翻译 {len(todo)} 句台词（已有译文跳过 {skipped} 句）")
    out = sv.llm.generate(TRANSLATE_TASK, system, user, TranslationOut, check=check, log=ctx.log,
                          manual=payload.get("manual") or {}, key=f"{TRANSLATE_TASK}:{episode_id}")
    km = {x.id: x.km.strip() for x in out.lines}
    written = []
    with sv.db.session() as s:
        for t in todo:
            sh = s.get(Shot, t["shot_id"])
            if sh is None or t["index"] >= len(sh.dialogue or []):
                continue
            dlg = [dict(d) for d in sh.dialogue]
            dlg[t["index"]].update(km=km[t["id"]], km_review="ai_draft")
            sh.dialogue = dlg
            written.append(f"{t['id']} {t['zh']} → {km[t['id']]}")
    # 日志要等写事务提交后再记：ctx.log 另开连接写 jobs 表，事务里调用会和自己抢 SQLite 写锁
    for line in written:
        ctx.log(line)
    return {"translated": len(todo), "skipped": skipped}


# ---------------------------------------------------------------- 配音
def _tts(sv, cache: Path, text: str, voice: str, rate: int) -> tuple[Path, float]:
    mp3 = cache / f"{input_hash('tts', text, voice, rate)}.mp3"
    if not mp3.exists() or mp3.stat().st_size == 0:
        tmp = mp3.with_suffix(".part.mp3")
        try:
            edge_tts_save(text, voice, rate, tmp)
        except Exception as e:
            raise JobError(f"Edge TTS 失败（{voice}，{rate:+d}%）：{e}") from e
        tmp.replace(mp3)
    return mp3, sv.ffmpeg.duration(mp3)


@HANDLERS.register("post.dub")
def run_dub(ctx, payload: dict) -> dict:
    """按成片时间轴给每句高棉语台词配音：先原速念，塞不下窗口就提速（最多 max_rate%）。"""
    sv = ctx.services
    opts = ExportOptions(**(payload.get("options") or {}))
    cut = load_cut(sv, int(payload["episode_id"]), allow_missing=bool(payload.get("allow_missing")))
    missing = lines_missing_km(cut)
    if missing:
        raise JobError(f"{', '.join(missing)} 还有台词没有高棉语译文，先跑字幕翻译或在镜头抽屉里填写")
    nov = speakers_without_voice(cut.dialogue, cut.voices)
    if nov:
        raise JobError(f"角色 {', '.join(nov)} 没有设置配音音色（如 km-KH-PisethNeural）")
    cfg = sv.cfg.dub
    secs, limits = clip_limits(sv, cut, opts.trim)
    durs, starts = planned_timeline(secs, limits, cut.transitions)
    lines = plan_dub_lines(cut.dialogue, starts, durs, gap=cfg.gap)
    manifest = dub_manifest_path(sv, cut)
    cache = manifest.parent
    rows = []
    for n, line in enumerate(lines, start=1):
        ctx.check_canceled()
        voice = cut.voices[line.speaker]
        mp3, dur = _tts(sv, cache, line.text, voice, 0)
        rate = fit_rate(dur, line.window, cfg.max_rate)
        if rate:
            mp3, dur = _tts(sv, cache, line.text, voice, rate)
        over = dur > line.window + 0.05
        code = cut.codes[line.shot]
        ctx.log(f"{code} 第 {line.index + 1} 句 {voice} {dur:.2f}s / 窗口 {line.window:.2f}s"
                + (f"（提速 {rate:+d}%）" if rate else "")
                + ("  仍超出窗口，尾巴会压到下一句" if over else ""))
        ctx.progress(n, len(lines))
        rows.append({"shot": line.shot, "code": code, "index": line.index,
                     "offset": round(line.start - starts[line.shot], 3),
                     "file": sv.media.rel(mp3), "seconds": round(dur, 3), "rate": rate,
                     "window": round(line.window, 3), "over": over, "voice": voice,
                     "text": line.text})
    manifest.write_text(json.dumps({"key": dub_key(cut, limits), "lines": rows}, ensure_ascii=False,
                                   indent=2), encoding="utf-8")
    over = sum(1 for r in rows if r["over"])
    return {"manifest": sv.media.rel(manifest), "lines": len(rows), "over": over}


def load_dub_manifest(sv, cut: Cut, limits) -> dict:
    p = dub_manifest_path(sv, cut)
    if not p.exists():
        raise JobError("还没有高棉语配音，先跑 post.dub（导出时勾选配音会自动排上）")
    data = json.loads(p.read_text(encoding="utf-8"))
    if data.get("key") != dub_key(cut, limits):
        raise JobError("配音和当前的视频、台词或音色对不上了，重跑 post.dub")
    return data


def episode_has_ai_draft(dialogue: list[list[dict]]) -> bool:
    return any((d.get("km") or "").strip() and d.get("km_review") != "checked"
               for dlg in dialogue for d in dlg)


def mark_exported(sv, episode_id: int) -> None:
    with sv.db.session() as s:
        s.get(Episode, episode_id).status = "exported"
