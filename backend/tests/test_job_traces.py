"""任务调用明细（job_traces）：LLM 调用、引擎提交、ffmpeg 命令的记录，掩码，落盘，接口。"""
import json
import sqlite3
import subprocess
import time

import pytest
from pydantic import BaseModel
from sqlalchemy import text

from backend.domain.db import Database
from backend.domain.models import Job, Project, Shot, StyleBible
from backend.engines.ffmpeg import FFmpeg
from backend.llm.base import LLMError
from backend.queue import trace as trace_mod
from backend.queue.handlers import HandlerRegistry
from backend.queue.manager import JobManager
from backend.queue.trace import JobTracer, list_traces, sanitize, trace_add, use_tracer
from backend.tests.helpers import FakeCtx, FakeRunner, make_gateway, make_services, seed_episode

SECRET = "sk-test-abcdefghijklmnopqrstuvwxyz123456"


class Out(BaseModel):
    n: int


def new_job(db, job_type="storyboard") -> int:
    with db.session() as s:
        j = Job(type=job_type, payload={}, status="running")
        s.add(j)
        s.flush()
        return j.id


def tracer(db, jid, tmp_path=None, **kw):
    return JobTracer(db, jid, data_dir=(tmp_path / "data") if tmp_path else None, **kw)


# ------------------------------------------------------------------ LLM
def test_gateway_records_each_attempt_with_prompts_reply_and_validation(db):
    jid = new_job(db)
    g, _ = make_gateway(["不是 JSON", '{"n": 1}', '{"n": 3}'])

    def check(o):
        if o.n != 3:
            raise ValueError("需要 n=3")

    with use_tracer(tracer(db, jid)):
        assert g.generate("storyboard", "SYS 提示词", "USER 提示词", Out, check=check,
                          step="storyboard").n == 3
    rows = list_traces(db, jid)
    assert [r["kind"] for r in rows] == ["llm"] * 3
    assert [r["seq"] for r in rows] == [1, 2, 3]
    first, second, third = (r["data"] for r in rows)
    assert rows[0]["title"] == "storyboard"
    assert first["provider"] == "fake" and first["attempt"] == 1 and first["max_attempts"] == 3
    assert first["system"] == "SYS 提示词" and first["user"] == "USER 提示词"
    assert first["reply"] == "不是 JSON" and first["status"] == "invalid" and first["error"]
    assert first["followups"] == []
    assert second["status"] == "invalid" and "需要 n=3" in second["error"]
    # 第二次起：上一次的回复和修正要求也发给了模型
    assert second["followups"][0] == {"role": "assistant", "content": "不是 JSON"}
    assert "不符合要求" in second["followups"][1]["content"]
    assert third["status"] == "ok" and third["parsed"] == {"n": 3}
    assert third["model"] == "fake" and "seconds" in third


def test_gateway_records_provider_error(db):
    jid = new_job(db)

    class Boom:
        id = "boom"

        def complete(self, system, messages, *, json_mode=True):
            raise LLMError("接口 500")

    g, _ = make_gateway([])
    g.providers["fake"] = Boom()
    with use_tracer(tracer(db, jid)), pytest.raises(LLMError):
        g.generate("script", "s", "u", Out, key="script_structure")
    (row,) = list_traces(db, jid)
    assert row["title"] == "script · script_structure"
    assert row["data"]["status"] == "error" and "接口 500" in row["data"]["error"]


def test_gateway_without_tracer_is_noop(db):
    g, _ = make_gateway(['{"n": 1}'])
    assert g.generate("t", "s", "u", Out).n == 1
    with db.session() as s:
        assert s.execute(text("SELECT count(*) FROM job_traces")).scalar() == 0


def test_manual_provider_records_pasted_reply(db):
    from backend.queue.errors import NeedsInput

    jid = new_job(db)
    g, _ = make_gateway([], kind="manual")
    with use_tracer(tracer(db, jid)), pytest.raises(NeedsInput):
        g.generate("script", "s", "u", Out, manual={})
    with use_tracer(tracer(db, jid)):
        assert g.generate("script", "s", "u", Out, manual={"script": '{"n": 5}'}).n == 5
    rows = list_traces(db, jid)
    assert [r["data"]["status"] for r in rows] == ["waiting_input", "ok"]
    assert [r["seq"] for r in rows] == [1, 2]   # 重新执行时接着往后排
    assert rows[1]["data"]["reply"] == '{"n": 5}' and rows[1]["data"]["provider"] == "manual"


def test_trace_inside_uncommitted_write_waits_for_session_end(tmp_path):
    """处理器在自己没提交的写事务里记明细：等会话结束再写库，不另开连接卡在写锁上。"""
    d = Database(tmp_path / "w.db")
    d.create_all()
    try:
        jid = new_job(d)
        t = tracer(d, jid)
        start = time.monotonic()
        with use_tracer(t), d.session() as s:
            s.add(Job(type="x", payload={}))
            s.flush()                       # 拿着写锁
            trace_add("note", "写事务里", {"a": 1})
            assert list_traces(d, jid) == []   # 还没写
        assert time.monotonic() - start < 5
        (row,) = list_traces(d, jid)
        assert row["kind"] == "note" and row["data"] == {"a": 1}
    finally:
        d.engine.dispose()


# ------------------------------------------------------------------ 掩码 / 落盘
def test_sanitize_masks_keys_and_secret_fields():
    data = {"api_key": SECRET, "headers": {"Authorization": f"Bearer {SECRET}"},
            "user": f"key is {SECRET} ok", "nested": [{"note": "AIza" + "x" * 35}],
            "usage": {"input_tokens": 12}}
    out = sanitize(data, [SECRET])
    text = json.dumps(out, ensure_ascii=False)
    assert SECRET not in text and "AIza" + "x" * 35 not in text
    assert out["api_key"] == "sk-…3456"
    assert out["headers"] == "（已隐藏）"
    assert out["user"] == "key is sk-…3456 ok"
    assert out["usage"] == {"input_tokens": 12}


def test_configured_key_never_reaches_trace_api(api):
    sv = api.app.state.services
    sv.api_keys.update("openai", api_key=SECRET)
    jid = new_job(sv.db)
    t = JobTracer(sv.db, jid, data_dir=sv.media.root,
                  secrets=trace_mod.collect_secrets(sv))
    with use_tracer(t):
        trace_add("llm", "storyboard", {"system": f"用这个 {SECRET[:-4]}XXXX 不算", "user": f"echo {SECRET}",
                                        "reply": f"Error: invalid key {SECRET}"})
    r = api.get(f"/api/jobs/{jid}/traces")
    assert r.status_code == 200
    assert SECRET not in r.text
    (row,) = r.json()["traces"]
    assert row["data"]["user"] == "echo sk-…3456"
    assert api.get("/api/jobs/999999/traces").status_code == 404


def test_big_field_spills_to_file(tmp_path, db, monkeypatch):
    monkeypatch.setattr(trace_mod, "FIELD_LIMIT", 50)
    jid = new_job(db)
    t = tracer(db, jid, tmp_path)
    with use_tracer(t):
        trace_add("llm", "storyboard", {"reply": "长" * 120, "workflow": {"k": "v" * 80}, "short": "ok"})
    (row,) = list_traces(db, jid)
    data = row["data"]
    assert data["short"] == "ok"
    assert data["reply"].startswith("长" * 50) and "已截断" in data["reply"]
    f = data["_files"]["reply"]
    assert f["size"] == 120
    p = tmp_path / "data" / "jobs" / str(jid) / f["file"]
    assert p.read_text(encoding="utf-8") == "长" * 120
    assert "workflow" in data["_files"]


def test_trace_file_endpoint(api):
    sv = api.app.state.services
    jid = new_job(sv.db)
    d = sv.media.root / "jobs" / str(jid)
    d.mkdir(parents=True)
    (d / "trace-001-reply.txt").write_text("全文", encoding="utf-8")
    r = api.get(f"/api/jobs/{jid}/trace-files/trace-001-reply.txt")
    assert r.status_code == 200 and r.text == "全文"
    assert api.get(f"/api/jobs/{jid}/trace-files/..%2Fstudio.db").status_code in (400, 404)
    assert api.get(f"/api/jobs/{jid}/trace-files/other.txt").status_code == 400


# ------------------------------------------------------------------ 引擎 / ffmpeg
def _shot(db, video_engine="h3_video"):
    proj_id, ep_id = seed_episode(db, video_engine=video_engine, image_engine="h3_image")
    with db.session() as s:
        proj = s.get(Project, proj_id)
        proj.style_bible = StyleBible(anchor_text="ANCHOR", negative="cartoon")
        proj.negative = "blurry"
        sh = Shot(episode_id=ep_id, order=0, code="S03", image_prompt="a woman in a studio",
                  motion_prompt="she looks up", audio_prompt="hum")
        s.add(sh)
        s.flush()
        return sh.id


def test_keyframe_records_engine_call(tmp_path, db):
    from backend.pipeline.keyframes import run_keyframe

    shot_id = _shot(db)
    runner = FakeRunner()
    sv = make_services(tmp_path, db, comfy=runner)
    jid = new_job(db, "image.keyframe")
    with use_tracer(JobTracer(db, jid, data_dir=sv.media.root)):
        res = run_keyframe(FakeCtx(sv, job_id=jid), {"shot_id": shot_id})
    rows = [r for r in list_traces(db, jid) if r["kind"] == "engine"]
    assert len(rows) == 1
    row = rows[0]
    d = row["data"]
    assert row["title"] == "出图 · h3_image · 首帧 S03"
    assert d["engine"] == "h3_image" and d["runner"] == "comfyui" and d["status"] == "ok"
    assert "a woman in a studio" in d["prompt"] and "ANCHOR" in d["prompt"]
    assert d["negative"] == "blurry, cartoon" and d["negative_sent"] is False
    assert d["params"]["aspect"] == "9:16" and isinstance(d["params"]["seed"], int)
    assert d["params"]["steps"] == 20
    assert d["workflow"] == runner.calls[0].workflow
    assert d["workflow_file"] == f"demo/runs/{jid}/workflow.api.json"
    assert d["outputs"] == [f"demo/runs/{jid}/out_1.png"]
    assert d["candidate_ids"] == res["candidate_ids"]
    assert d["candidate_files"][0].startswith("demo/media/first_frame/")


def test_engine_failure_is_recorded(tmp_path, db):
    from backend.pipeline.keyframes import run_keyframe

    shot_id = _shot(db)
    sv = make_services(tmp_path, db, comfy=FakeRunner(fail=RuntimeError("ComfyUI 炸了")))
    jid = new_job(db, "image.keyframe")
    with use_tracer(JobTracer(db, jid, data_dir=sv.media.root)), pytest.raises(RuntimeError):
        run_keyframe(FakeCtx(sv, job_id=jid), {"shot_id": shot_id})
    (row,) = [r for r in list_traces(db, jid) if r["kind"] == "engine"]
    assert row["data"]["status"] == "error" and "ComfyUI 炸了" in row["data"]["error"]


def test_ffmpeg_run_is_recorded_but_ffprobe_is_not(db, monkeypatch):
    jid = new_job(db, "export.episode")
    ff = FFmpeg("ffmpeg", "ffprobe")
    monkeypatch.setattr(ff, "_exec", lambda args, cwd=None, text=True:
                        subprocess.CompletedProcess(args, 0, "{}", ""))
    with use_tracer(tracer(db, jid)):
        ff.run(["ffprobe", "-v", "error", "a.mp4"])
        ff.run(["ffmpeg", "-y", "-i", "a.mp4", "-filter_complex", "[0:v]scale=1:1[v]",
                "-map", "[v]", "out.mp4"])
    (row,) = list_traces(db, jid)
    assert row["kind"] == "ffmpeg" and row["title"] == "ffmpeg · out.mp4"
    assert row["data"]["filter_complex"] == "[0:v]scale=1:1[v]"
    assert row["data"]["argv"][0] == "ffmpeg" and row["data"]["status"] == "ok"


# ------------------------------------------------------------------ 队列 / 迁移
def test_manager_installs_tracer_for_each_job(db):
    reg = HandlerRegistry()

    @reg.register("demo")
    def handler(ctx, payload):
        trace_add("note", "处理器里记一条", {"x": payload["x"]})
        return {"ok": True}

    m = JobManager(db, reg, poll=0.02)
    m.start()
    try:
        jid = m.submit("demo", {"x": 7})
        end = time.monotonic() + 5
        while m.get(jid)["status"] != "done":
            assert time.monotonic() < end
            time.sleep(0.02)
    finally:
        m.stop()
    (row,) = m.traces(jid)
    assert row["title"] == "处理器里记一条" and row["data"] == {"x": 7}
    assert trace_mod.current() is None   # 不在任务里时不记


def test_migration_creates_job_traces(tmp_path):
    d = Database(tmp_path / "m.db")
    d.upgrade()
    d.engine.dispose()
    c = sqlite3.connect(d.path)
    try:
        ddl = c.execute("SELECT sql FROM sqlite_master WHERE name='job_traces'").fetchone()[0]
    finally:
        c.close()
    assert "job_id" in ddl and "seq" in ddl and "kind" in ddl and "data" in ddl
    assert "REFERENCES jobs (id) ON DELETE CASCADE" in ddl


def test_job_without_calls_gets_a_note(db):
    reg = HandlerRegistry()

    @reg.register("noop")
    def handler(ctx, payload):
        return {"skipped": True}

    m = JobManager(db, reg, poll=0.02)
    m.start()
    try:
        jid = m.submit("noop", {})
        end = time.monotonic() + 5
        while m.get(jid)["status"] != "done":
            assert time.monotonic() < end
            time.sleep(0.02)
    finally:
        m.stop()
    (row,) = m.traces(jid)
    assert row["kind"] == "note" and row["title"] == "没有调用记录"
