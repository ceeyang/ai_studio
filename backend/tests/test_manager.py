import threading
import time
from pathlib import Path

import pytest

from backend.domain.models import Job
from backend.queue.errors import JobError, NeedsInput
from backend.queue.handlers import HandlerRegistry
from backend.queue.manager import JobManager


def wait_for(pred, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = pred()
        if v:
            return v
        time.sleep(0.02)
    raise AssertionError("等待超时")


class RecordingBus:
    def __init__(self):
        self.events = []

    def publish(self, e):
        self.events.append(e)


@pytest.fixture
def managers():
    made = []
    yield made
    for m in made:
        m.stop()


def make(db, reg, managers, start=True):
    m = JobManager(db, reg, RecordingBus(), poll=0.02)
    managers.append(m)
    if start:
        m.start()
    return m


def wait_status(m, jid, *statuses):
    return wait_for(lambda: (d := m.get(jid))["status"] in statuses and d)


def test_job_runs_to_done(db, managers):
    reg = HandlerRegistry()

    @reg.register("echo")
    def echo(ctx, payload):
        ctx.log("开始")
        return {"x": payload["x"]}

    m = make(db, reg, managers)
    jid = m.submit("echo", {"x": 1})
    j = wait_status(m, jid, "done")
    assert j["result"] == {"x": 1}
    assert "开始" in j["log"]
    assert any(e["type"] == "job" and e["job"]["status"] == "done" for e in m.bus.events)


def test_failures_record_error(db, managers):
    reg = HandlerRegistry()

    @reg.register("biz")
    def biz(ctx, payload):
        raise JobError("镜头 S03 还没有选定首帧")

    @reg.register("bug")
    def bug(ctx, payload):
        raise RuntimeError("boom")

    m = make(db, reg, managers)
    a, b = m.submit("biz"), m.submit("bug")
    assert wait_status(m, a, "failed")["error"] == "镜头 S03 还没有选定首帧"
    jb = wait_status(m, b, "failed")
    assert jb["error"].startswith("RuntimeError: boom")
    assert "Traceback" in jb["log"]


def test_cancel_queued(db, managers):
    reg = HandlerRegistry()
    reg.register("noop")(lambda ctx, p: None)
    m = make(db, reg, managers)
    m.pause()
    jid = m.submit("noop")
    assert m.cancel(jid) is True
    assert m.get(jid)["status"] == "canceled"
    m.resume()
    time.sleep(0.1)
    assert m.get(jid)["status"] == "canceled"


def test_cancel_running(db, managers):
    reg = HandlerRegistry()
    started = threading.Event()

    @reg.register("loop", gpu=True)
    def loop(ctx, payload):
        started.set()
        while True:
            ctx.check_canceled()
            time.sleep(0.01)

    m = make(db, reg, managers)
    jid = m.submit("loop")
    assert started.wait(3)
    assert m.cancel(jid) is True
    wait_status(m, jid, "canceled")


def test_recover_marks_running_failed(db, managers):
    with db.session() as s:
        j = Job(type="x", status="running")
        s.add(j)
        s.flush()
        jid = j.id
    m = make(db, HandlerRegistry(), managers, start=False)
    assert m.recover() == 1
    d = m.get(jid)
    assert d["status"] == "failed" and d["error"].startswith("interrupted")


def test_gpu_jobs_run_one_at_a_time(db, managers):
    reg = HandlerRegistry()
    lock = threading.Lock()
    state = {"active": 0, "max": 0}

    @reg.register("g", gpu=True)
    def g(ctx, payload):
        with lock:
            state["active"] += 1
            state["max"] = max(state["max"], state["active"])
        time.sleep(0.05)
        with lock:
            state["active"] -= 1

    m = make(db, reg, managers)
    ids = [m.submit("g") for _ in range(3)]
    for jid in ids:
        wait_status(m, jid, "done")
    assert state["max"] == 1


def test_family_preference_and_switch_hook(db, managers):
    reg = HandlerRegistry()
    order = []

    @reg.register("g", gpu=True)
    def g(ctx, payload):
        order.append(payload["name"])

    m = make(db, reg, managers, start=False)
    switches = []
    m.on_family_switch = lambda old, new: switches.append((old, new))
    a = m.submit("g", {"name": "A"}, model_family="zimage")
    b = m.submit("g", {"name": "B"}, model_family="h3")
    c = m.submit("g", {"name": "C"}, model_family="zimage")
    m.start()
    for jid in (a, b, c):
        wait_status(m, jid, "done")
    assert order == ["A", "C", "B"]
    assert switches == [("zimage", "h3")]


def test_dependency_waits_for_upstream(db, managers):
    reg = HandlerRegistry()
    release = threading.Event()

    @reg.register("slow")
    def slow(ctx, payload):
        release.wait(5)

    reg.register("ok")(lambda ctx, p: {"ran": True})
    m = make(db, reg, managers)
    up = m.submit("slow")
    down = m.submit("ok", depends_on=[up])
    time.sleep(0.2)
    assert m.get(down)["status"] == "queued"
    release.set()
    assert wait_status(m, down, "done")["result"] == {"ran": True}


def test_dependency_failure_fails_dependent(db, managers):
    reg = HandlerRegistry()

    @reg.register("bad")
    def bad(ctx, payload):
        raise JobError("上游坏了")

    reg.register("ok")(lambda ctx, p: None)
    m = make(db, reg, managers)
    up = m.submit("bad")
    down = m.submit("ok", depends_on=[up])
    d = wait_status(m, down, "failed")
    assert "上游" in d["error"]


def test_needs_input_then_provide(db, managers):
    reg = HandlerRegistry()

    @reg.register("ask")
    def ask(ctx, payload):
        got = (payload.get("manual") or {}).get("k")
        if got is None:
            raise NeedsInput({"key": "k", "user": "请粘贴结果"})
        return {"got": got}

    m = make(db, reg, managers)
    jid = m.submit("ask")
    d = wait_status(m, jid, "waiting_input")
    assert d["result"]["input_request"]["user"] == "请粘贴结果"
    m.provide_input(jid, "答案")
    assert wait_status(m, jid, "done")["result"] == {"got": "答案"}
    with pytest.raises(JobError):
        m.provide_input(jid, "再来")


def test_gpu_exclusive_blocks_gpu_jobs(db, managers):
    reg = HandlerRegistry()
    reg.register("g", gpu=True)(lambda ctx, p: None)
    m = make(db, reg, managers)
    handover = []
    m.on_gpu_handover = lambda: handover.append(1)
    with m.gpu_exclusive():
        jid = m.submit("g")
        time.sleep(0.3)
        assert m.get(jid)["status"] == "queued"
    wait_status(m, jid, "done")
    assert handover == [1]


def test_retry_creates_new_job_with_override(db, managers):
    reg = HandlerRegistry()

    @reg.register("flaky")
    def flaky(ctx, payload):
        if not payload.get("fixed"):
            raise JobError("还没修")
        return {"ok": True}

    m = make(db, reg, managers)
    first = m.submit("flaky", {"a": 1}, project_id=7)
    wait_status(m, first, "failed")
    second = m.retry(first, {"fixed": True})
    assert second != first
    d = wait_status(m, second, "done")
    assert d["payload"] == {"a": 1, "fixed": True} and d["project_id"] == 7
    with pytest.raises(JobError):
        m.retry(second)


def test_queue_state_and_list(db, managers):
    reg = HandlerRegistry()
    reg.register("noop")(lambda ctx, p: None)
    m = make(db, reg, managers)
    m.pause()
    a = m.submit("noop", project_id=1)
    m.submit("noop", project_id=2)
    st = m.queue_state()
    assert st["paused"] is True and st["counts"]["queued"] == 2
    assert [j["id"] for j in m.list_jobs(project_id=1)] == [a]
    assert len(m.list_jobs(statuses=["queued"])) == 2
    with pytest.raises(LookupError):
        m.get(9999)


def test_gpu_worker_survives_unserializable_result(db, managers):
    reg = HandlerRegistry()

    @reg.register("bad_result", gpu=True)
    def bad_result(ctx, payload):
        return {"path": Path("x")}  # 不能被 JSON 列序列化，commit 时会炸

    reg.register("g", gpu=True)(lambda ctx, p: None)

    m = make(db, reg, managers)
    jid = m.submit("bad_result")
    d = wait_status(m, jid, "failed")
    assert d["error"]

    gpu_thread = next(t for t in m._threads if t.name == "gpu-worker")
    assert gpu_thread.is_alive()

    # worker 没死，后续任务要能正常跑完
    jid2 = m.submit("g")
    wait_status(m, jid2, "done")


def test_cancel_serializes_with_claim(db, managers):
    reg = HandlerRegistry()
    reg.register("noop")(lambda ctx, p: None)
    m = make(db, reg, managers, start=False)
    jid = m.submit("noop")

    acquired = threading.Event()
    release = threading.Event()

    def hold_claim_lock():
        with m._claim_lock:
            acquired.set()
            release.wait(5)

    holder = threading.Thread(target=hold_claim_lock)
    holder.start()
    assert acquired.wait(2)

    result = {}

    def do_cancel():
        result["ok"] = m.cancel(jid)

    canceler = threading.Thread(target=do_cancel)
    canceler.start()
    time.sleep(0.1)
    assert "ok" not in result  # cancel() 应该在等 _claim_lock，没能跑完
    release.set()
    canceler.join(2)
    holder.join(2)

    assert result["ok"] is True
    assert m.get(jid)["status"] == "canceled"


def test_provide_input_serializes_with_claim(db, managers):
    reg = HandlerRegistry()
    reg.register("noop")(lambda ctx, p: None)
    m = make(db, reg, managers, start=False)
    jid = m.submit("noop")
    with db.session() as s:
        j = s.get(Job, jid)
        j.status = "waiting_input"
        j.result = {"input_request": {"key": "k"}}

    acquired = threading.Event()
    release = threading.Event()

    def hold_claim_lock():
        with m._claim_lock:
            acquired.set()
            release.wait(5)

    holder = threading.Thread(target=hold_claim_lock)
    holder.start()
    assert acquired.wait(2)

    result = {}

    def do_provide():
        result["d"] = m.provide_input(jid, "答案")

    provider = threading.Thread(target=do_provide)
    provider.start()
    time.sleep(0.1)
    assert "d" not in result  # provide_input() 应该在等 _claim_lock
    release.set()
    provider.join(2)
    holder.join(2)

    assert result["d"]["status"] == "queued"
    assert result["d"]["payload"]["manual"]["k"] == "答案"
