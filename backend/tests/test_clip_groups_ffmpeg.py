"""组镜拆分实跑（真 ffmpeg）：一条 10 秒视频里红 0–3 秒、绿 3–7 秒、蓝 7–10 秒（Veo 一次生成按时间戳切的三段），
拆给三个成员镜头：场景检测找到切点、成员起点对齐到切点，合成后每个成员的画面从它自己的颜色开始，
成片里的切点落在 asm.starts 上。没有 ffmpeg 就跳过。"""
import shutil
import subprocess

import pytest

import backend.pipeline  # noqa: F401
from backend.domain.models import Shot
from backend.domain.slots import add_candidate, get_or_create_slot
from backend.engines.ffmpeg import FFmpeg
from backend.pipeline.post import clip_limits, load_cut
from backend.tests.helpers import make_services, seed_episode

pytestmark = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                                reason="没有 ffmpeg")


def three_color_clip(path):
    """10 秒：红 3 秒、绿 4 秒、蓝 3 秒，带一路正弦音轨（像 Veo 的环境声一直响到结尾）。"""
    parts = "".join(f"color=c={c}:s=180x320:r=24:d={d}[v{i}];"
                    for i, (c, d) in enumerate((("red", 3), ("lime", 4), ("navy", 3))))
    graph = parts + "[v0][v1][v2]concat=n=3:v=1:a=0[v]"
    subprocess.run(["ffmpeg", "-y", "-filter_complex", graph, "-f", "lavfi", "-i",
                    "sine=frequency=220:duration=10", "-map", "[v]", "-map", "0:a", "-t", "10",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "240", "-c:a", "aac", str(path)],
                   check=True, capture_output=True)
    return path


def rgb_at(video, t):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1",
                          "-vf", "scale=1:1:flags=area", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         check=True, capture_output=True).stdout
    return tuple(raw[:3])


def dominant(rgb):
    return "rgb"[max(range(3), key=lambda i: rgb[i])]


def test_group_clip_split_into_members_real_ffmpeg(tmp_path, db):
    sv = make_services(tmp_path, db)
    sv.ffmpeg = FFmpeg()
    _, ep_id = seed_episode(db)
    src = sv.media.new_media_path("demo", "video", "mp4")
    three_color_clip(src)
    with db.session() as s:
        ids = []
        # 计划时长 3.4 / 3.4 / 3.2：成员起点计划在 3.4、6.8，检测到的切点在 3.0、7.0（±0.6 内）→ 对齐
        for i, d in enumerate((3.4, 3.4, 3.2)):
            sh = Shot(episode_id=ep_id, order=i, code=f"S{i + 1:02d}", duration=d, clip_group=1,
                      transition_out="dissolve_0.5")
            s.add(sh)
            s.flush()
            ids.append(sh.id)
        add_candidate(s, get_or_create_slot(s, "shot", ids[0], "video"), file_path=sv.media.rel(src),
                      engine="gemini_video", params={}, input_hash="h", job_id=None)
    cut = load_cut(sv, ep_id)
    assert cut.starts_at == [0.0, pytest.approx(3.0, abs=0.05), pytest.approx(7.0, abs=0.05)]
    _secs, limits = clip_limits(sv, cut, trim=True)
    out = tmp_path / "out.mp4"
    asm = sv.ffmpeg.assemble(cut.clips, out, width=180, height=320, fps=24, transitions=cut.transitions,
                             work_dir=tmp_path / "work", max_seconds=limits, starts_at=cut.starts_at)
    # 每个成员取到自己那一段：3 / 4 / 剩下 3 秒
    assert asm.durations == [pytest.approx(3.0, abs=0.06), pytest.approx(4.0, abs=0.06),
                             pytest.approx(3.0, abs=0.06)]
    for st, dur, want in zip(asm.starts, asm.durations, "rgb"):
        assert dominant(rgb_at(out, st + dur / 2)) == want
    # 每个成员的第一帧就是它自己的颜色（截在切点上，没有混进上一段）
    for i, want in enumerate("rgb"):
        n = tmp_path / "work" / f"n{i:03d}.mp4"
        assert dominant(rgb_at(n, 0.0)) == want
        assert dominant(rgb_at(n, max(asm.durations[i] - 0.05, 0))) == want
    # 成片里的切点就在 asm.starts 上（硬切）
    cuts = sv.ffmpeg.scene_cuts(out)
    for st in asm.starts[1:]:
        assert any(abs(c - st) < 0.15 for c in cuts), (cuts, asm.starts)
