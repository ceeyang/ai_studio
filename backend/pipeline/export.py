"""合成导出：按镜头顺序把选定视频拼成成片；可选按分镜时长裁尾、高棉语配音、双语字幕、片尾卡。

顺序：合成画面（裁尾 + xfade）→ 叠配音（只重编码音轨）→ 烧字幕并接片尾卡（一次编码）。
字幕时间 = 台词 start/end + 该镜在成片里的起点（xfade 偏移），起点取合成时实际量到的每镜时长。
"""
from __future__ import annotations

import shutil
from datetime import datetime

from backend.domain.models import Export
from backend.pipeline.post import (OUTPUT_FPS, clip_limits, episode_has_ai_draft, lines_missing_km,
                                   load_cut, load_dub_manifest, mark_exported)
from backend.post.dub import build_dub_mix_cmd
from backend.post.options import ExportOptions
from backend.post.render import CardSpec, build_finish_cmd, stage_fonts
from backend.post.subtitles import build_endcard_ass, build_subtitles_ass, timeline_cues
from backend.queue.errors import JobError
from backend.queue.handlers import HANDLERS

OUTPUT_SIZES = {"9:16": (1080, 1920), "16:9": (1920, 1080)}
DEFAULT_CARD_BG = "#0b1a33"


def card_color(raw: str | None) -> str:
    c = (raw or DEFAULT_CARD_BG).strip().lstrip("#")
    if len(c) != 6 or any(ch not in "0123456789abcdefABCDEF" for ch in c):
        raise JobError(f"片尾卡背景色要写成 #rrggbb，收到 {raw!r}")
    return "0x" + c.lower()


@HANDLERS.register("export.episode")
def run_export(ctx, payload: dict) -> dict:
    sv = ctx.services
    episode_id = int(payload["episode_id"])
    opts = ExportOptions(**(payload.get("options") or {}))
    cut = load_cut(sv, episode_id, allow_missing=bool(payload.get("allow_missing")))
    if cut.aspect not in OUTPUT_SIZES:
        raise JobError(f"不支持的画幅 {cut.aspect}")
    if opts.subtitles == "zh+km":
        missing = lines_missing_km(cut)
        if missing:
            raise JobError(f"{', '.join(missing)} 还有台词没有高棉语译文，先跑字幕翻译")
    card = cut.end_card or {}
    if opts.end_card and not any((x.get("text") or "").strip() for x in card.get("lines") or []):
        raise JobError("项目还没有设置片尾卡，先在导演台或 studio project end-card 里填写")
    width, height = OUTPUT_SIZES[cut.aspect]
    variant = cut.aspect.replace(":", "x")
    secs, limits = clip_limits(sv, cut, opts.trim)
    manifest = load_dub_manifest(sv, cut, limits) if opts.dub == "km" else None

    out = sv.media.export_path(
        cut.slug, f"ep{cut.number:02d}_{variant}{opts.tag()}_{datetime.now():%Y%m%d_%H%M%S}.mp4")
    work = sv.media.run_dir(cut.slug, ctx.job_id) / "work"
    post = manifest is not None or opts.subtitles != "none" or opts.end_card
    trims = [{"code": c, "clip": round(s, 3), "used": round(lim, 3)}
             for c, s, lim in zip(cut.codes, secs, limits) if lim]
    if trims:
        ctx.log("按分镜时长裁尾：" + "，".join(f"{t['code']} {t['clip']:.2f}→{t['used']:.2f}s"
                                         for t in trims))
    ctx.log(f"合成 {len(cut.clips)} 个镜头 → {width}x{height}@{OUTPUT_FPS}")
    asm = sv.ffmpeg.assemble(cut.clips, work / "base.mp4" if post else out, width=width,
                             height=height, fps=OUTPUT_FPS, transitions=cut.transitions,
                             work_dir=work, max_seconds=limits)
    cur = asm.path
    over = 0
    if manifest is not None:
        rows = manifest["lines"]
        over = sum(1 for r in rows if r.get("over"))
        lines = [(sv.media.abs(r["file"]), asm.starts[r["shot"]] + r["offset"], r["seconds"])
                 for r in rows]
        ctx.log(f"叠高棉语配音 {len(lines)} 句，原音轨压到 {sv.cfg.dub.bed_db:g} dB")
        dubbed = work / "dub.mp4"
        sv.ffmpeg.run(build_dub_mix_cmd(sv.ffmpeg.ffmpeg, cur, lines, dubbed,
                                        bed_db=sv.cfg.dub.bed_db, duck_db=sv.cfg.dub.duck_db,
                                        loudness=sv.cfg.dub.loudness))
        cur = dubbed
    card_seconds = 0.0
    if opts.subtitles != "none" or opts.end_card:
        st = sv.cfg.subtitles
        fonts = stage_fonts(st.fonts_dir, st.font_files, work)
        subs_name = None
        if opts.subtitles != "none":
            cues = timeline_cues(cut.dialogue, asm.starts, asm.durations)
            (work / "subs.ass").write_text(
                build_subtitles_ass(cues, width, height, mode=opts.subtitles, zh_font=st.zh_font,
                                    km_font=st.km_font), encoding="utf-8")
            subs_name = "subs.ass"
            ctx.log(f"烧字幕（{opts.subtitles}）{len(cues)} 条")
        spec = None
        if opts.end_card:
            card_seconds = float(card.get("seconds") or 3.0)
            (work / "card.ass").write_text(
                build_endcard_ass(card["lines"], width, height, card_seconds,
                                  default_font=st.latin_font), encoding="utf-8")
            spec = CardSpec("card.ass", card_seconds, width, height, OUTPUT_FPS,
                            color=card_color(card.get("background")))
            ctx.log(f"接片尾卡 {card_seconds:g}s（{len(card['lines'])} 行）")
        sv.ffmpeg.run(build_finish_cmd(sv.ffmpeg.ffmpeg, cur, out, subs_ass=subs_name, card=spec,
                                       has_audio=sv.ffmpeg.has_audio(cur), fonts_dir=fonts),
                      cwd=work)
    elif cur != out:
        shutil.move(str(cur), out)
    shutil.rmtree(work, ignore_errors=True)
    rel = sv.media.rel(out)
    settings = {"width": width, "height": height, "fps": OUTPUT_FPS, "clips": len(cut.clips),
                "missing": cut.missing, **opts.model_dump(),
                "duration": round(asm.total + card_seconds, 3), "main_seconds": round(asm.total, 3),
                "end_card_seconds": card_seconds, "trims": trims}
    if opts.needs_km:
        settings["km_review"] = "ai_draft" if episode_has_ai_draft(cut.dialogue) else "checked"
    if manifest is not None:
        settings["dub_lines_over"] = over
    with sv.db.session() as s:
        e = Export(episode_id=episode_id, variant=variant, file_path=rel, settings=settings)
        s.add(e)
        s.flush()
        export_id = e.id
    mark_exported(sv, episode_id)
    return {"export_id": export_id, "file": rel}
