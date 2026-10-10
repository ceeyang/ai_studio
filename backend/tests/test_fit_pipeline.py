"""dialogue.fit_khmer 任务（假 TTS + 假 LLM）、fit → videos → export 任务链、API、
配音对齐实际说话（假说话段）、无原声底导出（真 ffmpeg）。"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import select

import backend.pipeline  # noqa: F401
from backend.domain.models import Character, Job, Shot
from backend.engines.ffmpeg import FFmpeg
from backend.pipeline.export import run_export
from backend.pipeline.fit_khmer import run_fit_khmer
from backend.pipeline.post import run_dub
from backend.pipeline.runs import RunOptions, submit_chain
from backend.post.fit import speech_chars
from backend.queue.handlers import HANDLERS
from backend.queue.manager import JobManager
from backend.tests.helpers import FakeCtx, give_selected, make_gateway, make_services, seed_episode
from backend.tests.test_post_pipeline import TimedFFmpeg, add_video, dialogue

KM_SECONDS = {"ជាង! គេប្រាប់ថា មួយនេះត្រូវដូរហើយ។": 3.2, "ប្ដូរ?": 1.0, "សុំមើលសិន។": 1.2}


def fake_tts(km_seconds=KM_SECONDS, rate=4.0):
    """高棉语按表给秒数；中文每字 1/rate 秒。文件里写秒数，TimedFFmpeg.duration 读回来。"""
    calls = []

    def save(text, voice, pct, out):
        calls.append((text, voice))
        sec = km_seconds[text] if text in km_seconds else speech_chars(text) / rate
        Path(out).write_text(str(sec))
    return save, calls


def seed(db):
    pid, ep_id = seed_episode(db)
    ids = []
    with db.session() as s:
        s.add(Character(project_id=pid, name="小雨", voice="km-KH-SreymomNeural"))
        s.add(Character(project_id=pid, name="陈师傅", voice="km-KH-PisethNeural"))
        for i, (dur, dlg, audio) in enumerate([
            (4.5, [{"speaker": "小雨", "zh": "师傅，别人都说这个得换。", "start": 1.6, "end": 4.45,
                    "km": "ជាង! គេប្រាប់ថា មួយនេះត្រូវដូរហើយ។", "km_review": "ai_draft"}],
             'She says "师傅，别人都说这个得换。" once. No music.'),
            (5.0, [{"speaker": "陈师傅", "zh": "换？", "start": 1.6, "end": 2.4},
                   {"speaker": "陈师傅", "zh": "先让我看看。", "start": 2.5, "end": 4.6,
                    "km": "សុំមើលសិន។", "km_review": "checked"}], "Chen speaks. No music."),
            (5.0, [], "Ambience only."),
        ]):
            sh = Shot(episode_id=ep_id, order=i, code=f"S{i + 1:02d}", dialogue=dlg, duration=dur,
                      audio_prompt=audio)
            s.add(sh)
            s.flush()
            ids.append(sh.id)
    return ep_id, ids


def shot(db, sid):
    with db.session() as s:
        sh = s.get(Shot, sid)
        return {"dialogue": sh.dialogue, "duration": sh.duration, "audio": sh.audio_prompt}


def test_fit_khmer_translates_measures_rewrites_and_retimes(tmp_path, db, monkeypatch):
    gw, prov = make_gateway([
        {"lines": [{"id": "S02.1", "km": "ប្ដូរ?"}]},                         # 补翻译
        {"lines": [{"id": "S01.1", "zh": "师傅，别人都说这个零件得换掉。"},   # 第 1 轮
                   {"id": "S02.1", "zh": "换掉？"}]},
        {"lines": [{"id": "S02.1", "zh": "换？要换吗"}]},                         # 第 2 轮
    ])
    sv = make_services(tmp_path, db, llm=gw, ffmpeg=TimedFFmpeg())
    ep_id, ids = seed(db)
    save, calls = fake_tts()
    monkeypatch.setattr("backend.pipeline.post.edge_tts_save", save)
    ctx = FakeCtx(sv)
    res = run_fit_khmer(ctx, {"episode_id": ep_id})

    assert "S02.1｜陈师傅｜" in prov.calls[0]["messages"][0]["content"]   # 先翻缺的那句
    r1 = prov.calls[1]["messages"][0]["content"]
    assert "id=S01.1" in r1 and "id=S02.1" in r1 and "id=S02.2" not in r1   # S02.2 原句已合格
    assert ("師傅" not in r1) and "目标字数≈" in r1
    assert ("师傅，别人都说这个得换。", "zh-CN-XiaoxiaoNeural") in calls       # 女声角色用晓晓
    assert ("先让我看看。", "zh-CN-YunxiNeural") in calls

    s1 = shot(db, ids[0])
    d = s1["dialogue"][0]
    assert d["zh_original"] == "师傅，别人都说这个得换。" and d["zh"] == "师傅，别人都说这个零件得换掉。"
    assert d["km_seconds"] == 3.2 and d["zh_seconds"] == pytest.approx(3.25)
    assert d["fit_ratio"] == pytest.approx(3.25 / 3.2, abs=1e-3) and d["fit_rounds"] == 1
    assert d["start"] == 1.6 and d["end"] == pytest.approx(4.8)
    assert s1["duration"] == pytest.approx(5.2)                       # 4.8 + 0.4 尾巴
    assert '"师傅，别人都说这个零件得换掉。"' in s1["audio"] and "[Dialogue pacing:" in s1["audio"]

    s2 = shot(db, ids[1])
    a, b = s2["dialogue"]
    assert a["km"] == "ប្ដូរ?" and a["km_review"] == "ai_draft"
    assert a["zh"] == "换？要换吗" and a["fit_rounds"] == 2 and a["fit_ratio"] == pytest.approx(1.0)
    assert b["zh"] == "先让我看看。" and b["zh_original"] == "先让我看看。" and b["fit_rounds"] == 0
    assert (a["start"], a["end"]) == (1.6, 2.6) and (b["start"], b["end"]) == (2.75, 3.95)
    assert s2["duration"] == 5.0                                       # 不缩短
    assert shot(db, ids[2])["dialogue"] == []                          # 没台词的镜头不动

    assert res["lines"] == 3 and res["ok"] == 3
    log = "\n".join(ctx.logs)
    assert "S01.1 小雨：「师傅，别人都说这个得换。」2.50s → 「师傅，别人都说这个零件得换掉。」3.25s" in log
    assert "高棉语 3.20s" in log and "S01 时长 4.5s → 5.2s" in log


def test_fit_khmer_rerun_keeps_lines_that_still_fit(tmp_path, db, monkeypatch):
    gw, prov = make_gateway([
        {"lines": [{"id": "S02.1", "km": "ប្ដូរ?"}]},
        {"lines": [{"id": "S01.1", "zh": "师傅，别人都说这个零件得换掉。"}, {"id": "S02.1", "zh": "换？要换吗"}]},
    ])
    sv = make_services(tmp_path, db, llm=gw, ffmpeg=TimedFFmpeg())
    ep_id, ids = seed(db)
    monkeypatch.setattr("backend.pipeline.post.edge_tts_save", fake_tts()[0])
    run_fit_khmer(FakeCtx(sv), {"episode_id": ep_id})
    n = len(prov.calls)
    ctx = FakeCtx(sv)
    res = run_fit_khmer(ctx, {"episode_id": ep_id})
    assert len(prov.calls) == n and res["kept"] == 3 and res["lines"] == 0
    assert shot(db, ids[0])["dialogue"][0]["zh"] == "师傅，别人都说这个零件得换掉。"


def test_fit_khmer_only_selected_shots(tmp_path, db, monkeypatch):
    sv = make_services(tmp_path, db, llm=None, ffmpeg=TimedFFmpeg())
    ep_id, ids = seed(db)
    monkeypatch.setattr("backend.pipeline.post.edge_tts_save", fake_tts()[0])
    with db.session() as s:   # S02 先有译文、原句就合格，不需要 LLM
        s.get(Shot, ids[1]).dialogue = [{"speaker": "陈师傅", "zh": "先让我看看", "start": 1.0,
                                         "end": 2.0, "km": "សុំមើលសិន។"}]
    res = run_fit_khmer(FakeCtx(sv), {"episode_id": ep_id, "shot_ids": [ids[1]]})
    assert res["lines"] == 1 and [c["code"] for c in res["shots"]] == ["S02"]
    assert "zh_original" not in shot(db, ids[0])["dialogue"][0]


# ---------------------------------------------------------------- 任务链 / API
def test_chain_fit_then_videos_export(tmp_path, db):
    sv = make_services(tmp_path, db)
    ep_id, ids = seed(db)
    manager = JobManager(db, HANDLERS, poll=0.02)
    opts = RunOptions(shots=[str(ids[0])], export={"dub": "km", "bed": False, "align": "detected"})
    jid, = submit_chain(manager, db, sv.engines, ep_id, ["fit_khmer", "videos", "export"], opts)
    with db.session() as s:
        j = s.get(Job, jid)
        assert j.type == "dialogue.fit_khmer"
        assert j.payload["shot_ids"] == [ids[0]] and j.payload["then"] == ["videos", "export"]
        assert j.payload["options"]["export"]["bed"] is False
    with pytest.raises(Exception, match="最前面"):
        submit_chain(manager, db, sv.engines, ep_id, ["videos", "fit_khmer"], RunOptions())


def test_fit_khmer_endpoint(api):
    api.post("/api/projects", json={"title": "demo"})
    ep = api.post("/api/projects/demo/episodes", json={"source_text": "x"}).json()
    sh = api.post(f"/api/episodes/{ep['id']}/shots", json={"dialogue": [
        {"speaker": "陈师傅", "zh": "换？", "start": 1.0, "end": 1.6}]}).json()
    r = api.post(f"/api/episodes/{ep['id']}/dialogue/fit-khmer",
                 json={"shot_ids": [sh["id"]], "then": ["videos"]})
    assert r.status_code == 400 and "S01 还没有选定首帧" in r.json()["detail"]
    give_selected(api.app.state.services.db, sh["id"], "first_frame")
    r = api.post(f"/api/episodes/{ep['id']}/dialogue/fit-khmer",
                 json={"shot_ids": [sh["id"]], "then": ["videos", "export"],
                       "export": {"subtitles": "zh+km", "dub": "km", "bed": False,
                                  "align": "detected", "end_card": True}})
    assert r.status_code == 200, r.text
    with api.app.state.services.db.session() as s:
        j = s.scalars(select(Job)).one()
        assert j.type == "dialogue.fit_khmer" and j.payload["shot_ids"] == [sh["id"]]
        assert j.payload["options"]["export"]["align"] == "detected"
    assert api.post(f"/api/episodes/{ep['id']}/dialogue/fit-khmer",
                    json={"then": ["script"]}).status_code == 422
    r = api.post(f"/api/episodes/{ep['id']}/run", json={"stage": "fit_khmer", "then": ["videos"]})
    assert r.status_code == 200
    r = api.patch(f"/api/shots/{sh['id']}", json={"dialogue": [
        {"speaker": "陈师傅", "zh": "换？要换吗", "zh_original": "换？", "km_seconds": 1.0,
         "zh_seconds": 1.0, "fit_ratio": 1.0, "start": 1.0, "end": 2.0}]})
    assert r.json()["dialogue"][0]["zh_original"] == "换？"


# ---------------------------------------------------------------- 配音对齐实际说话
class SpeechFFmpeg(TimedFFmpeg):
    def __init__(self, segments):
        super().__init__()
        self.segments = segments

    def speech_segments(self, path, **kw):
        return self.segments


def test_dub_align_detected_moves_lines_to_speech_onsets(tmp_path, db, monkeypatch):
    sv = make_services(tmp_path, db, ffmpeg=SpeechFFmpeg([(0.3, 0.5), (1.9, 2.4), (3.0, 4.2)]))
    pid, ep_id = seed_episode(db)
    with db.session() as s:
        s.add(Character(project_id=pid, name="陈师傅", voice="km-KH-PisethNeural"))
        sh = Shot(episode_id=ep_id, order=0, code="S01", duration=5.0, dialogue=[
            {"speaker": "陈师傅", "zh": "换？", "km": "ប្ដូរ?", "start": 1.6, "end": 2.4},
            {"speaker": "陈师傅", "zh": "先让我看看。", "km": "សុំមើលសិន។", "start": 2.5, "end": 4.6}])
        s.add(sh)
        s.flush()
        sid = sh.id
    add_video(sv, sid)
    monkeypatch.setattr("backend.pipeline.post.edge_tts_save", fake_tts()[0])
    ctx = FakeCtx(sv)
    res = run_dub(ctx, {"episode_id": ep_id, "options": {"dub": "km", "align": "detected"}})
    rows = json.loads(sv.media.abs(res["manifest"]).read_text(encoding="utf-8"))["lines"]
    assert [(r["offset"], r["planned"], r["detected"]) for r in rows] == [(1.9, 1.6, True),
                                                                         (3.0, 2.5, True)]
    assert rows[0]["window"] == pytest.approx(3.0 - 1.9 - 0.1)
    assert any("S01 说话段 0.30–0.50、1.90–2.40、3.00–4.20" in m for m in ctx.logs)
    # 导出时要的是 detected 的清单：拿 timeline 去对会判作废
    from backend.pipeline.post import load_cut, load_dub_manifest, clip_limits
    cut = load_cut(sv, ep_id)
    _, limits = clip_limits(sv, cut, True)
    assert load_dub_manifest(sv, cut, limits, "detected")["align"] == "detected"
    with pytest.raises(Exception, match="重跑 post.dub"):
        load_dub_manifest(sv, cut, limits, "timeline")


# ---------------------------------------------------------------- 无原声底导出（真 ffmpeg）
needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                                  reason="没有 ffmpeg")


def loud_clip(path, seconds=3.0):
    """灰底 + 整段 300Hz "普通话"（响）。"""
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=0x404040:s=270x480:r=24:d={seconds}",
                    "-f", "lavfi", "-i", f"sine=frequency=300:duration={seconds}", "-t", str(seconds),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)],
                   check=True, capture_output=True)
    return path


def tone_tts(text, voice, rate, out):
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=880:duration=0.6",
                    "-c:a", "libmp3lame", str(out)], check=True, capture_output=True)


@needs_ffmpeg
def test_export_without_bed_keeps_only_the_dub(tmp_path, db, monkeypatch):
    ff = FFmpeg()
    sv = make_services(tmp_path, db, ffmpeg=ff)
    pid, ep_id = seed_episode(db)
    with db.session() as s:
        s.add(Character(project_id=pid, name="陈师傅", voice="km-KH-PisethNeural"))
        sh = Shot(episode_id=ep_id, order=0, code="S01", duration=3.0, dialogue=[
            {"speaker": "陈师傅", "zh": "能修的", "km": "ជួសជុលបាន", "start": 1.0, "end": 1.6}])
        s.add(sh)
        s.flush()
        sid = sh.id
    add_video(sv, sid, loud_clip(tmp_path / "a.mp4"))
    monkeypatch.setattr("backend.pipeline.post.edge_tts_save", tone_tts)
    opts = {"subtitles": "zh+km", "dub": "km", "bed": False}
    run_dub(FakeCtx(sv), {"episode_id": ep_id, "options": opts})
    res = run_export(FakeCtx(sv, job_id=3), {"episode_id": ep_id, "options": opts})
    out = sv.media.abs(res["file"])
    assert "_zhkm_dubkm_nobed_" in out.name
    segs = ff.speech_segments(out)
    # 原音轨整段 300Hz 都没了，只剩 1.0s 处 0.6s 的配音
    assert len(segs) == 1
    assert segs[0][0] == pytest.approx(1.0, abs=0.12) and segs[0][1] == pytest.approx(1.6, abs=0.12)
