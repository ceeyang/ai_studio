"""配乐（E）：压低窗口、ffmpeg 命令构造、导出接入、上传接口；有 ffmpeg 时用 lavfi 正弦波真混一次。"""
import shutil
import subprocess
from pathlib import Path

import pytest

import backend.pipeline  # noqa: F401  注册全部处理器
from backend.domain.models import Episode, Export, Shot
from backend.domain.music import bgm_of, clear_bgm, set_bgm
from backend.domain.slots import add_candidate, get_or_create_slot
from backend.pipeline.export import run_export
from backend.pipeline.runs import RunOptions, submit_chain
from backend.post.music import build_music_mix_cmd, duck_expr, duck_windows
from backend.post.options import ExportOptions, export_label
from backend.queue.errors import JobError
from backend.queue.handlers import HANDLERS
from backend.queue.manager import JobManager
from backend.tests.helpers import FakeCtx, FakeFFmpeg, make_services, seed_episode


# ------------------------------------------------------------ 纯函数
def test_duck_windows_pad_merge_and_trim():
    assert duck_windows([]) == []
    assert duck_windows([(1.0, 2.0)]) == [(0.85, 2.25)]
    # 挨得近的合并（间隔小于一个斜坡），远的分开；按成片长度截断；空的、倒的丢掉
    assert duck_windows([(5.0, 6.0), (1.0, 2.0), (2.3, 3.0), (9.9, 12.0), (4, 4)], total=10.0) == [
        (0.85, 3.25), (4.85, 6.25), (9.75, 10.0)]
    assert duck_windows([(0.05, 1.0)]) == [(0.0, 1.25)]


def test_duck_expr_ramps():
    e = duck_expr([(1.0, 2.0)], -10.0, 0.3)
    assert e == "1-0.6838*min(1,clip((t-0.700)/0.3,0,1)*clip((2.300-t)/0.3,0,1))"


def test_music_cmd_with_original_audio_and_ducking():
    cmd = build_music_mix_cmd("ffmpeg", "base.mp4", "bgm.mp3", "out.mp4", total=12.5,
                              volume_db=-18, fade_in=1, fade_out=2, windows=[(1.0, 2.0)],
                              has_audio=True)
    assert cmd[:6] == ["ffmpeg", "-y", "-i", "base.mp4", "-stream_loop", "-1"]
    assert cmd[6:8] == ["-i", "bgm.mp3"]
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "[1:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,atrim=0:12.500" in graph
    assert "volume=-18.0dB" in graph
    assert "volume='1-0.6838*min(1," in graph and ":eval=frame" in graph
    assert "afade=t=in:st=0:d=1.00" in graph and "afade=t=out:st=10.500:d=2.00" in graph
    assert "[orig][bgm]amix=inputs=2:normalize=0:dropout_transition=0:duration=first[mix]" in graph
    # 最后限幅：峰值压在 -1.5 dBFS，AAC 后约 -1 dBTP，不削波
    assert graph.endswith("[mix]alimiter=limit=0.841:level=0:latency=1[a]")
    assert cmd[cmd.index("-c:v") + 1] == "copy" and cmd[-1] == "out.mp4"
    assert cmd[cmd.index("-t") + 1] == "12.500"


def test_music_cmd_without_audio_or_ducking():
    cmd = build_music_mix_cmd("ffmpeg", "b.mp4", "m.wav", "o.mp4", total=3, volume_db=-6,
                              fade_in=0, fade_out=0, windows=[], has_audio=False)
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "eval=frame" not in graph and "afade" not in graph and "[0:a]" not in graph
    assert graph.endswith("[bgm];[bgm]alimiter=limit=0.841:level=0:latency=1[a]")


def test_options_tag_and_label():
    o = ExportOptions(music=True, end_card=True)
    assert o.tag() == "_bgm_card"
    assert export_label("9x16", {**o.model_dump(), "duration": 12.0}) == "9:16 · 配乐 · 片尾卡 · 12.0 秒"
    assert ExportOptions().tag() == "" and "配乐" not in export_label("9x16", ExportOptions().model_dump())
    with pytest.raises(ValueError):
        ExportOptions(music_db=-80)


# ------------------------------------------------------------ 导出接入
class RunFFmpeg(FakeFFmpeg):
    """在假 ffmpeg 上记下 run 的命令，并把命令最后的输出文件写出来。"""

    def __init__(self):
        super().__init__()
        self.cmds = []

    def run(self, args, cwd=None):
        self.cmds.append([str(a) for a in args])
        Path(args[-1]).write_bytes(b"out")
        return ""


def seed(tmp_path, db):
    ff = RunFFmpeg()
    sv = make_services(tmp_path, db, ffmpeg=ff)
    _, ep_id = seed_episode(db)
    with db.session() as s:
        for i in range(2):
            sh = Shot(episode_id=ep_id, order=i, code=f"S{i + 1:02d}",
                      dialogue=[{"speaker": "a", "zh": "你好", "start": 1.0, "end": 2.0}])
            s.add(sh)
            s.flush()
            p = sv.media.new_media_path("demo", "video", "mp4")
            p.write_bytes(b"v")
            add_candidate(s, get_or_create_slot(s, "shot", sh.id, "video"),
                          file_path=sv.media.rel(p), engine="h3_video", params={},
                          input_hash="h", job_id=None)
    return sv, ff, ep_id


def put_bgm(sv, ep_id, name="song.mp3", data=b"ID3music"):
    with sv.db.session() as s:
        return set_bgm(s, sv.media, s.get(Episode, ep_id), data, name).id


def test_export_with_music_mixes_and_labels(tmp_path, db):
    sv, ff, ep_id = seed(tmp_path, db)
    with pytest.raises(JobError, match="还没有上传配乐"):
        run_export(FakeCtx(sv), {"episode_id": ep_id, "options": {"music": True}})
    put_bgm(sv, ep_id)
    ctx = FakeCtx(sv, job_id=7)
    res = run_export(ctx, {"episode_id": ep_id, "options": {"music": True, "music_db": -20,
                                                           "music_fade_out": 3}})
    mix = [c for c in ff.cmds if "-stream_loop" in c]
    assert len(mix) == 1
    graph = mix[0][mix[0].index("-filter_complex") + 1]
    assert "volume=-20.0dB" in graph and "afade=t=out" in graph and "eval=frame" in graph
    # 两句台词（每镜 1–2 秒），成片时间轴上第二镜从 xfade 偏移处开始：两个压低窗口
    assert graph.count("clip((t-") == 2
    assert res["file"].endswith(".mp4") and "_bgm_" in res["file"]
    assert any("叠配乐 -20 dB" in m and "2 段台词处自动压低" in m for m in ctx.logs)
    with db.session() as s:
        e = s.get(Export, res["export_id"])
        assert e.settings["music"] is True
        assert "配乐" in export_label(e.variant, e.settings)
    assert sv.media.abs(res["file"]).is_file()


def test_export_music_without_ducking(tmp_path, db):
    sv, ff, ep_id = seed(tmp_path, db)
    put_bgm(sv, ep_id)
    run_export(FakeCtx(sv), {"episode_id": ep_id, "options": {"music": True, "music_duck": False}})
    graph = ff.cmds[0][ff.cmds[0].index("-filter_complex") + 1]
    assert "eval=frame" not in graph


def test_submit_export_music_needs_bgm_before_any_job(tmp_path, db):
    sv, _, ep_id = seed(tmp_path, db)
    m = JobManager(db, HANDLERS, poll=0.02)
    with pytest.raises(JobError, match="还没有上传配乐"):
        submit_chain(m, db, sv.engines, ep_id, ["videos", "export"],
                     RunOptions(export={"music": True}))
    assert m.list_jobs() == []
    put_bgm(sv, ep_id)
    ids = submit_chain(m, db, sv.engines, ep_id, ["export"], RunOptions(export={"music": True}))
    assert m.get(ids[-1])["payload"]["options"]["music"] is True


def test_bgm_replace_and_clear(tmp_path, db):
    sv, _, ep_id = seed(tmp_path, db)
    with db.session() as s:
        with pytest.raises(ValueError, match="配乐只支持"):
            set_bgm(s, sv.media, s.get(Episode, ep_id), b"x", "a.txt")
    a = put_bgm(sv, ep_id)
    b = put_bgm(sv, ep_id, "b.wav", b"RIFF")
    with db.session() as s:
        assert bgm_of(s, ep_id).id == b != a
        clear_bgm(s, ep_id)
        assert bgm_of(s, ep_id) is None


def test_bgm_api(api):
    api.post("/api/projects", json={"title": "提词员", "slug": "demo"})
    ep = api.post("/api/projects/demo/episodes", json={"number": 1, "title": "t"}).json()
    url = f"/api/episodes/{ep['id']}/bgm"
    assert api.get(url).json() is None
    r = api.post(url, files={"file": ("我的歌.mp3", b"ID3xx", "audio/mpeg")})
    assert r.status_code == 200
    body = r.json()
    assert body["filename"] == "我的歌.mp3" and body["url"].startswith("/media/demo/media/bgm/")
    assert api.get(body["url"]).status_code == 200
    assert api.get(url).json()["candidate_id"] == body["candidate_id"]
    assert api.post(url, files={"file": ("x.exe", b"MZ", "application/octet-stream")}).status_code == 400
    assert api.delete(url).status_code == 204
    assert api.get(url).json() is None
    assert api.get("/api/episodes/9999/bgm").status_code == 404


# ------------------------------------------------------------ 真 ffmpeg
@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="没有 ffmpeg")
def test_real_ffmpeg_music_mix_ducks_under_dialogue(tmp_path):
    import numpy as np

    from backend.engines.ffmpeg import FFmpeg

    base = tmp_path / "base.mp4"
    music = tmp_path / "music.wav"
    # 3 秒画面 + 静音音轨；配乐是 1 秒的 440Hz 正弦（要循环到 3 秒）
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=64x64:r=24:d=3",
                    "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "3", "-shortest",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", str(base)], check=True, capture_output=True)
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=1",
                    str(music)], check=True, capture_output=True)
    out = tmp_path / "out.mp4"
    ff = FFmpeg()
    ff.run(build_music_mix_cmd(ff.ffmpeg, base, music, out, total=3.0, volume_db=-6,
                               fade_in=0, fade_out=0, windows=[(1.2, 2.0)], has_audio=True))
    assert out.is_file() and ff.has_audio(out)
    assert abs(ff.duration(out) - 3.0) < 0.15
    pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", str(out), "-map", "0:a:0", "-ac", "1",
                          "-ar", "48000", "-f", "f32le", "-"], check=True, capture_output=True).stdout
    x = np.frombuffer(pcm, dtype=np.float32)

    def rms(a, b):
        seg = x[int(a * 48000):int(b * 48000)]
        return float(np.sqrt(np.mean(seg ** 2)))

    loud, ducked, late = rms(0.2, 0.8), rms(1.35, 1.85), rms(2.5, 2.9)
    assert loud > 0.02 and abs(late - loud) < 0.2 * loud   # lavfi 正弦振幅 1/8；循环到了结尾
    db = 20 * np.log10(ducked / loud)
    assert -12.0 < db < -8.0                               # 台词窗口里再压 10 dB
