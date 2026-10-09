"""真 ffmpeg 跑一遍带选项的导出：裁尾、烧双语字幕、叠配音（edge-tts 用正弦波代替）、接片尾卡。没有 ffmpeg 就跳过。"""
import shutil
import subprocess

import pytest

import backend.pipeline  # noqa: F401
from backend.domain.models import Character, Export, Project, Shot
from backend.engines.ffmpeg import FFmpeg
from backend.pipeline.export import run_export
from backend.pipeline.post import run_dub
from backend.tests.helpers import FakeCtx, make_services, seed_episode
from backend.tests.test_post_pipeline import add_video

pytestmark = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                                reason="没有 ffmpeg")

CARD = {"seconds": 1.5, "background": "#0b1a33", "lines": [
    {"text": "កម្ពុជារថយន្តលេខរៀងទី1", "font": "Khmer UI", "size": 90, "bold": True},
    {"text": "柬埔寨1号铭车", "font": "Microsoft YaHei", "size": 80, "bold": True},
    {"text": "Telegram 0318891666", "font": "Arial", "size": 60}]}


def gray_clip(path, seconds):
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=0x404040:s=270x480:r=24:d={seconds}",
                    "-f", "lavfi", "-i", f"sine=frequency=330:duration={seconds}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)],
                   check=True, capture_output=True)
    return path


def fake_tts(text, voice, rate, out):
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=880:duration=0.6",
                    "-c:a", "libmp3lame", str(out)], check=True, capture_output=True)


def region_max(video, t, y0, y1):
    """在 t 秒处截一帧，返回 [y0, y1) 行范围内的最大亮度（0–255）。"""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1",
                          "-vf", f"crop=1080:{y1 - y0}:0:{y0},format=gray", "-f", "rawvideo", "-"],
                         check=True, capture_output=True).stdout
    return max(raw)


def test_export_with_subtitles_dub_and_end_card(tmp_path, db, monkeypatch):
    ff = FFmpeg()
    sv = make_services(tmp_path, db, ffmpeg=ff)
    pid, ep_id = seed_episode(db)
    src = [gray_clip(tmp_path / "a.mp4", 2.0), gray_clip(tmp_path / "b.mp4", 2.0)]
    with db.session() as s:
        s.get(Project, pid).end_card = CARD
        s.add(Character(project_id=pid, name="陈师傅", voice="km-KH-PisethNeural"))
        ids = []
        for i, (dur, dlg) in enumerate([
                (1.5, [{"speaker": "陈师傅", "zh": "能修的，我们绝对不换。",
                        "km": "អ្វីដែលជួសជុលបាន យើងមិនដូរឡើយ។", "start": 0.2, "end": 1.0}]),
                (2.0, [{"speaker": "陈师傅", "zh": "美元、瑞尔都收。", "km": "ទទួលទាំងដុល្លារ និងរៀល។",
                        "km_review": "checked", "start": 0.5, "end": 1.8}])]):
            sh = Shot(episode_id=ep_id, order=i, code=f"S{i + 1:02d}", duration=dur, dialogue=dlg)
            s.add(sh)
            s.flush()
            ids.append(sh.id)
    for sid, p in zip(ids, src):
        add_video(sv, sid, p)
    monkeypatch.setattr("backend.pipeline.post.edge_tts_save", fake_tts)
    opts = {"subtitles": "zh+km", "dub": "km", "end_card": True}
    run_dub(FakeCtx(sv), {"episode_id": ep_id, "options": opts})
    res = run_export(FakeCtx(sv, job_id=9), {"episode_id": ep_id, "options": opts})

    out = sv.media.abs(res["file"])
    assert "_zhkm_dubkm_card_" in out.name
    with db.session() as s:
        st = s.get(Export, res["export_id"]).settings
    # 第 1 镜 2.0s 裁到 1.65s（台词 1.0s 就说完）；硬切 0.05s + 一帧
    assert st["trims"] == [{"code": "S01", "clip": 2.0, "used": 1.65}]
    main = st["main_seconds"]
    assert abs(main - (1.667 + 2.0 - 0.05 - 1 / 24)) < 0.06
    assert st["duration"] == pytest.approx(main + 1.5, abs=1e-3)
    assert st["km_review"] == "ai_draft" and st["subtitles"] == "zh+km" and st["dub"] == "km"
    assert abs(ff.video_duration(out) - st["duration"]) < 0.1
    assert ff.has_audio(out)
    assert (ff.run([ff.ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
                    "stream=width,height", "-of", "csv=p=0", str(out)]).strip() == "1080,1920")
    # 字幕区（画面下方 21% 往上一块）：有台词时有白字，台词之间没有
    assert region_max(out, 0.6, 1300, 1520) > 200
    assert region_max(out, 1.2, 1300, 1520) < 120
    # 片尾卡：深色底上有字，字幕不会延续到片尾卡
    assert region_max(out, main + 1.0, 800, 1100) > 200
    assert region_max(out, main + 1.0, 1300, 1520) < 120
