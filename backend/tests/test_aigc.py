"""AI 生成内容标识：角标 ASS、收尾命令里的角标滤镜和元数据、AI 参与比例、导出接入、项目设置接口。"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

import backend.pipeline  # noqa: F401
from backend.domain.models import Export, Project, Shot
from backend.domain.slots import add_candidate, get_or_create_slot
from backend.engines.ffmpeg import FFmpeg
from backend.pipeline.export import run_export
from backend.post.aigc import (DEFAULT_LABEL_TEXT, DEFAULT_PRODUCER, ai_ratio, build_metadata,
                               episode_title, project_aigc, work_id)
from backend.post.options import ExportOptions, export_label
from backend.post.render import CardSpec, build_finish_cmd, ffmpeg_has_filter, metadata_args
from backend.post.subtitles import ass_plain, build_label_ass
from backend.tests.helpers import FakeCtx, FakeFFmpeg, make_services, seed_episode

META = build_metadata(title="提词员 第1集：直播事故", producer='星河"影业"', work="demo-E01", ratio=50,
                      created="2026-10-12T02:00:00")


# ------------------------------------------------------------------ 角标 ASS
def events(ass: str) -> list[str]:
    return [line for line in ass.splitlines() if line.startswith("Dialogue:")]


def test_label_ass_shows_for_first_seconds_in_top_left_by_default():
    ass = build_label_ass(DEFAULT_LABEL_TEXT, 1080, 1920, position="top_left", seconds=3.0,
                          font="Noto Sans CJK SC")
    (ev,) = events(ass)
    assert ev.startswith("Dialogue: 1,0:00:00.00,0:00:03.00,AIGC,")
    assert ev.endswith("{\\fad(0,300)}本片由 AI 辅助制作")
    style = next(line for line in ass.splitlines() if line.startswith("Style: AIGC,"))
    fields = style.split(",")
    assert fields[1] == "Noto Sans CJK SC" and fields[18] == "7"   # 小键盘 7 = 左上
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass


@pytest.mark.parametrize("pos,align", [("top_right", "9"), ("bottom_left", "1"), ("bottom_right", "3")])
def test_label_ass_positions(pos, align):
    ass = build_label_ass("AI", 1920, 1080, position=pos, seconds=5, font="F")
    style = next(line for line in ass.splitlines() if line.startswith("Style: AIGC,"))
    assert style.split(",")[18] == align
    assert events(ass)[0].split(",")[2] == "0:00:05.00"


def test_label_ass_always_on_has_no_fade_and_lasts_forever():
    (ev,) = events(build_label_ass("AI 辅助", 1080, 1920, position="top_left", seconds=None, font="F"))
    assert ev.split(",")[2] == "99:00:00.00" and "\\fad" not in ev


def test_label_ass_escapes_override_braces_backslashes_and_newlines():
    text = '本片{\\b1}由 "AI"\\N辅助\n制作'
    assert ass_plain(text) == '本片｛＼b1｝由 "AI"＼N辅助 制作'
    (ev,) = events(build_label_ass(text, 1080, 1920, position="top_left", seconds=3, font="F"))
    body = ev.split(",", 9)[9]
    assert body == '{\\fad(0,300)}本片｛＼b1｝由 "AI"＼N辅助 制作'


def test_label_ass_rejects_bad_position_and_empty_text():
    with pytest.raises(ValueError):
        build_label_ass("AI", 1080, 1920, position="center", seconds=3, font="F")
    with pytest.raises(ValueError):
        build_label_ass("  \n ", 1080, 1920, position="top_left", seconds=3, font="F")


# ------------------------------------------------------------------ 收尾命令
def arg_after(cmd, flag):
    return cmd[cmd.index(flag) + 1]


def metadata_of(cmd) -> dict[str, str]:
    return dict(cmd[i + 1].split("=", 1) for i, a in enumerate(cmd) if a == "-metadata")


def test_finish_cmd_label_only_burns_label_and_writes_metadata():
    cmd = build_finish_cmd("ffmpeg", "base.mp4", "out.mp4", subs_ass=None, card=None, has_audio=True,
                           label_ass="label.ass", metadata=META)
    assert arg_after(cmd, "-vf") == "ass=label.ass:fontsdir=_fonts"
    assert arg_after(cmd, "-c:v") == "libx264" and arg_after(cmd, "-c:a") == "copy"
    assert arg_after(cmd, "-movflags") == "+faststart+use_metadata_tags"
    meta = metadata_of(cmd)
    assert meta["aigc_label"] == "AI辅助制作" and meta["aigc_work_id"] == "demo-E01"
    assert meta["aigc_ratio"] == "50" and meta["aigc_tool"] == "ai_studio"
    assert meta["aigc_producer"] == '星河"影业"'          # 引号原样（参数不过 shell）
    assert meta["title"] == "提词员 第1集：直播事故"
    assert json.loads(meta["comment"])["work_id"] == "demo-E01"
    assert json.loads(meta["AIGC"]) == {"Label": "1", "ContentProducer": '星河"影业"',
                                        "ProduceID": "demo-E01", "ReservedCode1": "",
                                        "ContentPropagator": "", "PropagateID": "",
                                        "ReservedCode2": ""}
    assert cmd[-1] == "out.mp4"


def test_finish_cmd_label_goes_after_subtitles():
    cmd = build_finish_cmd("ffmpeg", "b.mp4", "o.mp4", subs_ass="subs.ass", card=None,
                           has_audio=False, label_ass="label.ass", fonts_dir="_f")
    assert arg_after(cmd, "-vf") == "ass=subs.ass:fontsdir=_f,ass=label.ass:fontsdir=_f"
    assert arg_after(cmd, "-movflags") == "+faststart" and "-metadata" not in cmd


def test_finish_cmd_with_card_puts_label_on_main_and_on_card_only_when_always():
    card = CardSpec("card.ass", 2.0, 1080, 1920, 24)
    cmd = build_finish_cmd("ffmpeg", "b.mp4", "o.mp4", subs_ass="subs.ass", card=card,
                           has_audio=True, label_ass="label.ass", metadata=META)
    graph = arg_after(cmd, "-filter_complex")
    main, on_card = graph.split(";")[:2]
    assert main == ("[0:v]ass=subs.ass:fontsdir=_fonts,ass=label.ass:fontsdir=_fonts,"
                    "setsar=1,format=yuv420p[mv]")
    assert "label.ass" not in on_card
    assert metadata_of(cmd)["aigc_work_id"] == "demo-E01"
    cmd = build_finish_cmd("ffmpeg", "b.mp4", "o.mp4", subs_ass=None, card=card, has_audio=False,
                           label_ass="label.ass", label_on_card=True)
    on_card = arg_after(cmd, "-filter_complex").split(";")[1]
    assert on_card.startswith("[1:v]ass=card.ass:fontsdir=_fonts,fade=t=in:st=0:d=0.500,"
                              "ass=label.ass:fontsdir=_fonts,")


def test_finish_cmd_metadata_only_copies_streams():
    cmd = build_finish_cmd("ffmpeg", "b.mp4", "o.mp4", subs_ass=None, card=None, has_audio=True,
                           metadata=META)
    assert arg_after(cmd, "-c") == "copy" and "-vf" not in cmd and "libx264" not in cmd
    assert cmd[cmd.index("-map"):cmd.index("-c")] == ["-map", "0:v:0", "-map", "0:a:0"]
    with pytest.raises(ValueError):
        build_finish_cmd("ffmpeg", "b.mp4", "o.mp4", subs_ass=None, card=None, has_audio=True)


def test_metadata_args_collapse_newlines():
    assert metadata_args({"title": "第一行\n第二行", "x": 3}) == ["-metadata", "title=第一行 第二行",
                                                              "-metadata", "x=3"]


# ------------------------------------------------------------------ 比例、编号、设置
def test_ai_ratio_counts_uploaded_footage_as_not_ai():
    assert ai_ratio(["h3_video", "kling", "web_gemini_video"]) == 100
    assert ai_ratio(["h3_video", "upload"]) == 50
    assert ai_ratio(["upload", "upload", "h3_video"]) == 33
    assert ai_ratio(["", "upload"]) == 0 and ai_ratio([]) == 0


def test_ids_titles_and_project_defaults():
    assert work_id("demo", 3) == "demo-E03"
    assert episode_title("提词员", 2) == "提词员 第2集"
    assert episode_title("提词员", 2, " 反转 ") == "提词员 第2集：反转"
    assert project_aigc(None) == {"label_text": DEFAULT_LABEL_TEXT, "producer": DEFAULT_PRODUCER}
    assert project_aigc({"label_text": " ", "producer": "星河影业"}) == {
        "label_text": DEFAULT_LABEL_TEXT, "producer": "星河影业"}


def test_export_options_defaults_and_label():
    o = ExportOptions()
    assert o.aigc_label and o.aigc_metadata and not o.aigc_label_always
    assert o.aigc_label_position == "top_left" and o.aigc_label_seconds == 3.0
    assert o.tag() == ""                                  # 默认开着，不进文件名
    with pytest.raises(ValueError):
        ExportOptions(aigc_label_position="middle")
    assert export_label("9x16", {"aigc_label": False}) == "9:16 · 无 AI 角标"
    assert export_label("9x16", {"aigc_label": True, "aigc_label_burned": False}) == "9:16 · AI 角标没烧上"
    assert export_label("9x16", {"aigc_label": True, "aigc_label_burned": True}) == "9:16"


# ------------------------------------------------------------------ 导出接入（假 ffmpeg）
class RecordFFmpeg(FakeFFmpeg):
    """记下收尾命令，同时把当时工作目录里的 label.ass 读出来（导出完工作目录会删掉）。"""

    def __init__(self):
        super().__init__()
        self.label_ass: str | None = None

    def run(self, args, cwd=None):
        if cwd is not None and (Path(cwd) / "label.ass").is_file():
            self.label_ass = (Path(cwd) / "label.ass").read_text(encoding="utf-8")
        return super().run(args, cwd)


def seed_two_shots(tmp_path, db, *, engines=("h3_video", "upload")):
    ff = RecordFFmpeg()
    sv = make_services(tmp_path, db, ffmpeg=ff)
    pid, ep_id = seed_episode(db)
    with db.session() as s:
        for i, engine in enumerate(engines):
            sh = Shot(episode_id=ep_id, order=i, code=f"S{i + 1:02d}", duration=5.0)
            s.add(sh)
            s.flush()
            p = sv.media.new_media_path("demo", "video", "mp4")
            p.write_bytes(b"v")
            add_candidate(s, get_or_create_slot(s, "shot", sh.id, "video"),
                          file_path=sv.media.rel(p), engine=engine, params={}, input_hash=f"h{i}",
                          job_id=None)
    return ff, sv, pid, ep_id


def test_export_label_on_without_other_post_steps_goes_through_finish(tmp_path, db, monkeypatch):
    monkeypatch.setattr("backend.pipeline.export.ffmpeg_has_filter", lambda ff, name: True)
    ff, sv, pid, ep_id = seed_two_shots(tmp_path, db)
    with db.session() as s:
        s.get(Project, pid).aigc = {"label_text": "AI 辅助制作 {演示}", "producer": "星河影业"}
    ctx = FakeCtx(sv, job_id=4)
    res = run_export(ctx, {"episode_id": ep_id, "options": {"aigc_label_position": "bottom_right"}})

    assert ff.calls[0]["out"].name == "base.mp4"            # 合成先写到工作目录，再收尾
    (cmd,) = ff.cmds
    assert arg_after(cmd, "-vf") == "ass=label.ass:fontsdir=_fonts"
    assert "-filter_complex" not in cmd                      # 没有片尾卡，不拼接，时间轴不变
    meta = metadata_of(cmd)
    assert meta["aigc_work_id"] == "demo-E01" and meta["aigc_producer"] == "星河影业"
    assert meta["aigc_ratio"] == "50" and meta["title"] == "提词员 第1集"
    assert cmd[-1] == str(sv.media.abs(res["file"]))
    (ev,) = events(ff.label_ass)
    assert ev.startswith("Dialogue: 1,0:00:00.00,0:00:03.00,AIGC,") and ev.endswith("AI 辅助制作 ｛演示｝")
    assert ",3,43,43,96,1" in ff.label_ass                   # 右下角，边距按 1080×1920 算
    with db.session() as s:
        st = s.get(Export, res["export_id"]).settings
    assert st["aigc_label_burned"] is True
    assert st["aigc"] == {"label_text": "AI 辅助制作 {演示}", "producer": "星河影业",
                          "work_id": "demo-E01", "ai_ratio": 50}
    assert any("烧 AI 标识角标" in m for m in ctx.logs)
    assert not (sv.media.run_dir("demo", 4) / "work").exists()


def test_export_label_always_on(tmp_path, db, monkeypatch):
    monkeypatch.setattr("backend.pipeline.export.ffmpeg_has_filter", lambda ff, name: True)
    ff, sv, _, ep_id = seed_two_shots(tmp_path, db, engines=("h3_video",))
    run_export(FakeCtx(sv), {"episode_id": ep_id,
                             "options": {"aigc_label_always": True, "aigc_metadata": False}})
    (cmd,) = ff.cmds
    assert "-metadata" not in cmd and arg_after(cmd, "-movflags") == "+faststart"
    assert events(ff.label_ass)[0].split(",")[2] == "99:00:00.00"
    assert DEFAULT_LABEL_TEXT in ff.label_ass


def test_export_without_libass_still_exports_with_metadata_and_warns(tmp_path, db, monkeypatch):
    monkeypatch.setattr("backend.pipeline.export.ffmpeg_has_filter", lambda ff, name: False)
    ff, sv, _, ep_id = seed_two_shots(tmp_path, db, engines=("h3_video", "kling"))
    ctx = FakeCtx(sv)
    res = run_export(ctx, {"episode_id": ep_id})
    (cmd,) = ff.cmds
    assert arg_after(cmd, "-c") == "copy" and "-vf" not in cmd
    assert metadata_of(cmd)["aigc_ratio"] == "100"
    assert any("没有 libass" in m for m in ctx.logs)
    with db.session() as s:
        e = s.get(Export, res["export_id"])
        assert e.settings["aigc_label_burned"] is False
        assert e.settings["aigc"]["label_text"] is None


def test_export_with_label_and_metadata_off_writes_assembly_directly(tmp_path, db):
    ff, sv, _, ep_id = seed_two_shots(tmp_path, db)
    res = run_export(FakeCtx(sv), {"episode_id": ep_id,
                                   "options": {"aigc_label": False, "aigc_metadata": False}})
    assert ff.cmds == [] and ff.calls[0]["out"] == sv.media.abs(res["file"])
    with db.session() as s:
        st = s.get(Export, res["export_id"]).settings
    assert st["aigc_label_burned"] is False and "aigc" not in st


# ------------------------------------------------------------------ 接口
def test_project_aigc_settings_roundtrip(api):
    api.post("/api/projects", json={"slug": "demo", "title": "提词员"})
    assert api.get("/api/projects/demo/aigc").json() == {"label_text": DEFAULT_LABEL_TEXT,
                                                         "producer": DEFAULT_PRODUCER}
    r = api.put("/api/projects/demo/aigc", json={"label_text": "AI 生成", "producer": "星河影业"})
    assert r.status_code == 200 and r.json() == {"label_text": "AI 生成", "producer": "星河影业"}
    assert api.get("/api/projects/demo/aigc").json()["producer"] == "星河影业"
    r = api.put("/api/projects/demo/aigc", json={"label_text": "", "producer": ""})
    assert r.json() == {"label_text": DEFAULT_LABEL_TEXT, "producer": DEFAULT_PRODUCER}
    assert api.put("/api/projects/demo/aigc", json={"label_text": "字" * 41}).status_code == 422
    assert api.get("/api/projects/nope/aigc").status_code == 404


# ------------------------------------------------------------------ 真 ffmpeg
needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                                  reason="没有 ffmpeg")


def tiny_clip(path, seconds=1.5):
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=0x202020:s=270x480:r=24:d={seconds}",
                    "-f", "lavfi", "-i", f"sine=frequency=330:duration={seconds}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)],
                   check=True, capture_output=True)
    return path


def probe_tags(path) -> dict[str, str]:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format_tags", "-of", "json",
                          str(path)], check=True, capture_output=True, text=True).stdout
    return json.loads(out)["format"].get("tags", {})


def real_export(tmp_path, db, options):
    ff = FFmpeg()
    sv = make_services(tmp_path, db, ffmpeg=ff)
    _, ep_id = seed_episode(db)
    with db.session() as s:
        sh = Shot(episode_id=ep_id, order=0, code="S01", duration=1.5)
        s.add(sh)
        s.flush()
        p = sv.media.new_media_path("demo", "video", "mp4")
        tiny_clip(p)
        add_candidate(s, get_or_create_slot(s, "shot", sh.id, "video"), file_path=sv.media.rel(p),
                      engine="h3_video", params={}, input_hash="h", job_id=None)
    res = run_export(FakeCtx(sv), {"episode_id": ep_id, "options": options})
    return ff, sv.media.abs(res["file"])


@needs_ffmpeg
def test_real_export_writes_metadata_tags(tmp_path, db):
    """只写元数据（不烧角标）：拷贝流，ffprobe 能读到标准键和自定义 aigc_* 键。"""
    _, out = real_export(tmp_path, db, {"aigc_label": False})
    tags = probe_tags(out)
    assert tags["aigc_label"] == "AI辅助制作" and tags["aigc_work_id"] == "demo-E01"
    assert tags["aigc_producer"] == DEFAULT_PRODUCER and tags["aigc_ratio"] == "100"
    assert tags["aigc_tool"] == "ai_studio" and tags["title"] == "提词员 第1集"
    assert json.loads(tags["comment"])["ai_ratio"] == 100
    assert json.loads(tags["AIGC"])["Label"] == "1"


@needs_ffmpeg
@pytest.mark.skipif(not (shutil.which("ffmpeg") and ffmpeg_has_filter("ffmpeg", "ass")),
                    reason="ffmpeg 没有 libass")
def test_real_export_burns_label_in_top_left_for_first_seconds(tmp_path, db):
    ff, out = real_export(tmp_path, db, {"aigc_label_seconds": 1.0})

    def corner_max(t):
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", str(out),
                              "-frames:v", "1", "-vf", "crop=600:200:0:60,format=gray",
                              "-f", "rawvideo", "-"], check=True, capture_output=True).stdout
        return max(raw)

    assert corner_max(0.3) > 200          # 片头左上角有白字
    assert corner_max(1.4) < 120          # 1 秒后淡出
    assert abs(ff.video_duration(out) - 1.5) < 0.1
    assert probe_tags(out)["aigc_work_id"] == "demo-E01"
