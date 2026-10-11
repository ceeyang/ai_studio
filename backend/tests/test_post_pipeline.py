"""后期任务：字幕翻译（假 LLM）、配音（edge-tts 打桩）、带选项的导出和任务链。"""
import json
from pathlib import Path

import pytest
from sqlalchemy import select

import backend.pipeline  # noqa: F401  注册全部处理器
from backend.domain.models import Character, Job, Shot
from backend.domain.slots import add_candidate, get_or_create_slot
from backend.pipeline.post import run_dub, run_translate
from backend.pipeline.runs import RunOptions, submit_chain
from backend.queue.errors import JobError
from backend.queue.handlers import HANDLERS
from backend.queue.manager import JobManager
from backend.tests.helpers import FakeCtx, FakeFFmpeg, make_gateway, make_services, seed_episode

D1 = [{"speaker": "小雨", "zh": "师傅，别人都说这个得换。", "start": 0.5, "end": 3.2}]
D2 = [{"speaker": "陈师傅", "zh": "换？", "start": 2.0, "end": 2.6},
      {"speaker": "陈师傅", "zh": "先让我看看。", "km": "សុំមើលសិន។", "km_review": "checked",
       "start": 2.8, "end": 4.6}]


def seed(db, dialogues=(D1, D2), durations=(4.5, 5.0)):
    pid, ep_id = seed_episode(db)
    ids = []
    with db.session() as s:
        s.add(Character(project_id=pid, name="小雨", voice="km-KH-SreymomNeural"))
        s.add(Character(project_id=pid, name="陈师傅", voice="km-KH-PisethNeural"))
        for i, (d, dur) in enumerate(zip(dialogues, durations)):
            sh = Shot(episode_id=ep_id, order=i, code=f"S{i + 1:02d}", dialogue=list(d), duration=dur)
            s.add(sh)
            s.flush()
            ids.append(sh.id)
    return ep_id, ids


def add_video(sv, shot_id, src: Path | None = None):
    p = sv.media.new_media_path("demo", "video", "mp4")
    p.write_bytes(src.read_bytes() if src else b"v")
    with sv.db.session() as s:
        add_candidate(s, get_or_create_slot(s, "shot", shot_id, "video"), file_path=sv.media.rel(p),
                      engine="h3_video", params={}, input_hash="h", job_id=None)
    return p


def dialogue(db, shot_id):
    with db.session() as s:
        return s.get(Shot, shot_id).dialogue


# ---------------------------------------------------------------- 翻译
def test_translate_fills_missing_km_as_ai_draft(tmp_path, db):
    gw, prov = make_gateway([{"lines": [{"id": "S01.1", "km": "ជាង! គេថាត្រូវដូរ។"},
                                        {"id": "S02.1", "km": "ដូរ?"}]}])
    sv = make_services(tmp_path, db, llm=gw)
    ep_id, ids = seed(db)
    res = run_translate(FakeCtx(sv), {"episode_id": ep_id, "glossary": {"柬埔寨1号铭车": "កម្ពុជា"}})
    assert res == {"translated": 2, "skipped": 1}
    user = prov.calls[0]["messages"][0]["content"]
    assert "S01.1｜小雨｜" in user and "S02.2" not in user        # 已有译文的不再翻
    assert "柬埔寨1号铭车 → កម្ពុជា" in user
    d1 = dialogue(db, ids[0])[0]
    assert d1["km"] == "ជាង! គេថាត្រូវដូរ។" and d1["km_review"] == "ai_draft"
    d2 = dialogue(db, ids[1])
    assert d2[0]["km_review"] == "ai_draft" and d2[1]["km"] == "សុំមើលសិន។"
    assert d2[1]["km_review"] == "checked"


def test_translate_rejects_wrong_ids_and_non_khmer_then_retries(tmp_path, db):
    gw, prov = make_gateway([{"lines": [{"id": "S01.1", "km": "hello"}]},
                             {"lines": [{"id": "S01.1", "km": "ជាង!"}, {"id": "S02.1", "km": "ដូរ?"}]}])
    sv = make_services(tmp_path, db, llm=gw)
    ep_id, _ = seed(db)
    run_translate(FakeCtx(sv), {"episode_id": ep_id})
    retry = prov.calls[1]["messages"][-1]["content"]
    assert "S02.1" in retry and "高棉文" in retry


def test_translate_force_retranslates_everything(tmp_path, db):
    gw, prov = make_gateway([{"lines": [{"id": "S01.1", "km": "ក"}, {"id": "S02.1", "km": "ខ"},
                                        {"id": "S02.2", "km": "គ"}]}])
    sv = make_services(tmp_path, db, llm=gw)
    ep_id, ids = seed(db)
    assert run_translate(FakeCtx(sv), {"episode_id": ep_id, "force": True})["translated"] == 3
    assert dialogue(db, ids[1])[1] == {**D2[1], "km": "គ", "km_review": "ai_draft"}


def test_translate_nothing_to_do(tmp_path, db):
    sv = make_services(tmp_path, db, llm=None)
    ep_id, _ = seed(db, dialogues=([],), durations=(5.0,))
    assert run_translate(FakeCtx(sv), {"episode_id": ep_id}) == {"translated": 0, "skipped": 0}


# ---------------------------------------------------------------- 配音
class TimedFFmpeg(FakeFFmpeg):
    """视频都当 5.167s；TTS 文件时长 = 文件里写的数字。"""

    def video_duration(self, path):
        return 5.167

    def duration(self, path):
        return float(Path(path).read_text())

    def trim_silence(self, src, dst):
        Path(dst).write_text(Path(src).read_text())
        return Path(dst)


def fake_tts(lengths):
    calls = []

    def save(text, voice, rate, out):
        calls.append((text, voice, rate))
        natural = lengths[text]
        Path(out).write_text(str(round(natural / (1 + rate / 100), 3)))
    return save, calls


def test_dub_fits_each_line_and_writes_manifest(tmp_path, db, monkeypatch):
    sv = make_services(tmp_path, db, ffmpeg=TimedFFmpeg())
    ep_id, ids = seed(db, dialogues=(
        [{**D1[0], "km": "ជាង!"}], [{**D2[0], "km": "ដូរ?"}, D2[1]]))
    for sid in ids:
        add_video(sv, sid)
    save, calls = fake_tts({"ជាង!": 2.0, "ដូរ?": 1.2, "សុំមើលសិន។": 2.0})
    monkeypatch.setattr("backend.pipeline.post.edge_tts_save", save)
    res = run_dub(FakeCtx(sv), {"episode_id": ep_id})
    # 第 1 镜裁到 4.65s；第 2 镜第 1 句窗口 = 2.8 - 2.0 - 0.1 = 0.7s，原速 1.2s → 提速封顶 +35% 仍超；
    # 第 2 句窗口到镜头结尾（5.167 - 2.8 - 0.1），原速放得下
    assert calls[0] == ("ជាង!", "km-KH-SreymomNeural", 0)
    assert ("ដូរ?", "km-KH-PisethNeural", 0) in calls
    assert ("ដូរ?", "km-KH-PisethNeural", 35) in calls     # 1.2/0.7 - 1 = 71% → 封顶 35
    manifest = json.loads(sv.media.abs(res["manifest"]).read_text(encoding="utf-8"))
    rows = manifest["lines"]
    assert [(r["shot"], r["offset"], r["rate"]) for r in rows] == [(0, 0.5, 0), (1, 2.0, 35), (1, 2.8, 0)]
    assert rows[1]["over"] is True and rows[2]["over"] is False
    assert res["lines"] == 3 and res["over"] == 1


def test_dub_reuses_cached_lines(tmp_path, db, monkeypatch):
    sv = make_services(tmp_path, db, ffmpeg=TimedFFmpeg())
    ep_id, ids = seed(db, dialogues=([{**D1[0], "km": "ជាង!"}],), durations=(5.0,))
    add_video(sv, ids[0])
    save, calls = fake_tts({"ជាង!": 2.0})
    monkeypatch.setattr("backend.pipeline.post.edge_tts_save", save)
    run_dub(FakeCtx(sv), {"episode_id": ep_id})
    run_dub(FakeCtx(sv), {"episode_id": ep_id})
    assert len(calls) == 1


def test_dub_requires_voice_and_translation(tmp_path, db, monkeypatch):
    sv = make_services(tmp_path, db, ffmpeg=TimedFFmpeg())
    ep_id, ids = seed(db)
    add_video(sv, ids[0])
    add_video(sv, ids[1])
    monkeypatch.setattr("backend.pipeline.post.edge_tts_save", fake_tts({})[0])
    with pytest.raises(JobError, match="S01.*没有高棉语"):
        run_dub(FakeCtx(sv), {"episode_id": ep_id})
    with db.session() as s:
        s.get(Shot, ids[0]).dialogue = [{**D1[0], "km": "ជាង!"}]
        s.get(Shot, ids[1]).dialogue = [{**D2[0], "km": "ដូរ?"}]
        s.scalar(select(Character).where(Character.name == "陈师傅")).voice = None
    with pytest.raises(JobError, match="陈师傅.*音色"):
        run_dub(FakeCtx(sv), {"episode_id": ep_id})


# ---------------------------------------------------------------- 任务链
@pytest.fixture
def manager(db):
    return JobManager(db, HANDLERS, poll=0.02)


def jobs(db):
    with db.session() as s:
        return [(j.id, j.type, j.payload, j.depends_on) for j in s.scalars(select(Job).order_by(Job.id))]


def test_chain_export_with_dub_and_km_subtitles(tmp_path, db, manager):
    sv = make_services(tmp_path, db)
    ep_id, _ = seed(db)
    opts = RunOptions(export={"subtitles": "zh+km", "dub": "km", "end_card": True})
    submit_chain(manager, db, sv.engines, ep_id, ["export"], opts)
    rows = jobs(db)
    assert [r[1] for r in rows] == ["subtitles.translate", "post.dub", "export.episode"]
    tr, dub, exp = rows
    assert tr[2] == {"episode_id": ep_id}
    assert dub[3] == [tr[0]] and exp[3] == [tr[0], dub[0]]
    assert exp[2]["options"] == {"subtitles": "zh+km", "dub": "km", "end_card": True, "trim": True,
                                    "bed": True, "align": "timeline"}


def test_chain_export_skips_translate_when_all_lines_have_km(tmp_path, db, manager):
    sv = make_services(tmp_path, db)
    ep_id, _ = seed(db, dialogues=([{**D1[0], "km": "ក"}],), durations=(5.0,))
    submit_chain(manager, db, sv.engines, ep_id, ["export"], RunOptions(export={"subtitles": "zh+km"}))
    assert [r[1] for r in jobs(db)] == ["export.episode"]


def test_chain_export_dub_needs_voices_up_front(tmp_path, db, manager):
    sv = make_services(tmp_path, db)
    ep_id, _ = seed(db)
    with db.session() as s:
        s.scalar(select(Character).where(Character.name == "小雨")).voice = ""
    with pytest.raises(JobError, match="小雨"):
        submit_chain(manager, db, sv.engines, ep_id, ["export"], RunOptions(export={"dub": "km"}))
    assert jobs(db) == []


def test_chain_plain_export_payload_unchanged(tmp_path, db, manager):
    sv = make_services(tmp_path, db)
    ep_id, _ = seed(db)
    submit_chain(manager, db, sv.engines, ep_id, ["export"], RunOptions())
    assert jobs(db)[0][2] == {"episode_id": ep_id}


class DbLogCtx(FakeCtx):
    """像真正的 JobContext 一样，日志另开一个会话写库。"""

    def log(self, msg):
        super().log(msg)
        with self.services.db.session() as s:
            s.add(Job(type="log-probe", payload={"msg": msg}))


@pytest.mark.timeout(20)  # 在写事务里记日志会卡在 SQLite 写锁上，直到超时
def test_translate_does_not_log_inside_its_write_transaction(tmp_path, db):
    gw, _ = make_gateway([{"lines": [{"id": "S01.1", "km": "ជាង!"}, {"id": "S02.1", "km": "ដូរ?"}]}])
    sv = make_services(tmp_path, db, llm=gw)
    ep_id, ids = seed(db)
    run_translate(DbLogCtx(sv), {"episode_id": ep_id})
    assert dialogue(db, ids[0])[0]["km"] == "ជាង!"


def test_tts_cancel_during_trim_leaves_no_half_written_cache(tmp_path, db, monkeypatch):
    """配音被取消在剪静音那一步：写了一半的 wav 不能留在缓存里（下次会被当成好的直接用），原始 mp3 也删掉。"""
    from backend.pipeline.post import _tts
    from backend.queue.errors import JobCanceled

    monkeypatch.setattr("backend.pipeline.post.edge_tts_save",
                        lambda text, voice, rate, out: Path(out).write_bytes(b"mp3"))

    class CancelInTrim(FakeFFmpeg):
        def trim_silence(self, src, dst):
            Path(dst).write_bytes(b"half")
            raise JobCanceled()

    sv = make_services(tmp_path, db, ffmpeg=CancelInTrim())
    cache = tmp_path / "cache"
    cache.mkdir()
    with pytest.raises(JobCanceled):
        _tts(sv, cache, "សួស្តី", "km-KH-PisethNeural", 0)
    assert list(cache.iterdir()) == []
