"""RunningHub 云端引擎：客户端（假 HTTP 传输，不联网）、映射文件、引擎拼 nodeInfoList、runner、注册表、
doctor、提交前检查。"""
import json
import shutil
import threading

import httpx
import pytest

from backend.config import ROOT, Config, RunningHubCfg, RunningHubEngineCfg
from backend.doctor import check_runninghub
from backend.engines.base import UPLOAD_PREFIX, ImageRequest, VideoRequest
from backend.engines.registry import EngineRegistry, check_ready, runtime_of
from backend.engines.runninghub.client import RunningHubClient, RunningHubError
from backend.engines.runninghub.runner import RunningHubRunner
from backend.queue.errors import JobCanceled, JobError

BASE = "https://rh.test"


class FakeRH:
    """记录请求、按脚本回应的假 RunningHub。"""

    def __init__(self, statuses=("QUEUED", "RUNNING", "SUCCESS"), outputs=None, create_code=0):
        self.requests: list[tuple[str, dict | bytes]] = []
        self.statuses = list(statuses)
        self.outputs = outputs if outputs is not None else [
            {"fileUrl": "https://cdn.rh.test/out/a.png", "fileType": "png", "nodeId": "9"}]
        self.create_code = create_code

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if req.url.host == "cdn.rh.test":
            self.requests.append((path, b""))
            return httpx.Response(200, content=b"PNGDATA")
        if path == "/task/openapi/upload":
            self.requests.append((path, req.read()))
            return httpx.Response(200, json={"code": 0, "msg": "success",
                                             "data": {"fileName": "api/ref123.png", "fileType": "image"}})
        body = json.loads(req.read() or b"{}")
        self.requests.append((path, body))
        if path in ("/task/openapi/create", "/task/openapi/ai-app/run"):
            if self.create_code:
                return httpx.Response(200, json={"code": self.create_code, "msg": "WORKFLOW_NOT_EXISTS"})
            return httpx.Response(200, json={"code": 0, "msg": "success",
                                             "data": {"taskId": "T1", "taskStatus": "QUEUED"}})
        if path == "/task/openapi/status":
            st = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
            return httpx.Response(200, json={"code": 0, "msg": "success", "data": st})
        if path == "/task/openapi/outputs":
            return httpx.Response(200, json={"code": 0, "msg": "success", "data": self.outputs})
        if path == "/task/openapi/cancel":
            return httpx.Response(200, json={"code": 0, "msg": "success"})
        return httpx.Response(404)


def client(fake, key="sk-rh"):
    return RunningHubClient(BASE, key, transport=httpx.MockTransport(fake), poll_interval=0)


# ---------------------------------------------------------------- 客户端
def test_create_workflow_task_sends_key_and_node_info():
    fake = FakeRH()
    nodes = [{"nodeId": "6", "fieldName": "text", "fieldValue": "a cat"}]
    assert client(fake).create(workflow_id="wf1", node_info_list=nodes) == "T1"
    path, body = fake.requests[0]
    assert path == "/task/openapi/create"
    assert body == {"apiKey": "sk-rh", "workflowId": "wf1", "nodeInfoList": nodes}


def test_create_webapp_task_uses_ai_app_run():
    fake = FakeRH()
    client(fake).create(webapp_id="app9", node_info_list=[])
    path, body = fake.requests[0]
    assert path == "/task/openapi/ai-app/run" and body["webappId"] == "app9"


def test_create_error_code_raises_chinese():
    with pytest.raises(RunningHubError, match="WORKFLOW_NOT_EXISTS"):
        client(FakeRH(create_code=804)).create(workflow_id="x", node_info_list=[])


def test_missing_key_rejected_before_any_request():
    fake = FakeRH()
    with pytest.raises(RunningHubError, match="RUNNINGHUB_API_KEY"):
        client(fake, key=None).create(workflow_id="x", node_info_list=[])
    assert fake.requests == []


def test_wait_polls_until_success_and_reports_states():
    fake = FakeRH(statuses=("QUEUED", "RUNNING", "RUNNING", "SUCCESS"))
    seen = []
    assert client(fake).wait("T1", cancel=threading.Event(), on_status=seen.append) == "SUCCESS"
    assert seen == ["QUEUED", "RUNNING", "SUCCESS"]       # 状态变化才回调
    assert all(b == {"apiKey": "sk-rh", "taskId": "T1"} for p, b in fake.requests)


def test_wait_failed_raises_with_outputs_reason():
    fake = FakeRH(statuses=("FAILED",), outputs={"failedReason": {"exception_message": "OOM"}})
    with pytest.raises(RunningHubError, match="OOM"):
        client(fake).wait("T1", cancel=threading.Event())


def test_wait_cancel_cancels_remote_task():
    fake = FakeRH(statuses=("RUNNING",))
    ev = threading.Event()
    ev.set()
    with pytest.raises(JobCanceled):
        client(fake).wait("T1", cancel=ev)
    assert ("/task/openapi/cancel", {"apiKey": "sk-rh", "taskId": "T1"}) in fake.requests


def test_wait_timeout():
    with pytest.raises(RunningHubError, match="超过"):
        client(FakeRH(statuses=("RUNNING",))).wait("T1", cancel=threading.Event(), timeout=0)


def test_outputs_and_download(tmp_path):
    fake = FakeRH()
    c = client(fake)
    outs = c.outputs("T1")
    assert outs[0]["fileUrl"].endswith("a.png")
    p = c.download(outs[0]["fileUrl"], tmp_path)
    assert p.name == "a.png" and p.read_bytes() == b"PNGDATA"


def test_download_name_cannot_escape_run_dir_and_dedupes(tmp_path):
    fake = FakeRH()
    c = client(fake)
    run = tmp_path / "run"
    p = c.download("https://cdn.rh.test/out/..%2F..%2Fx.png", run)
    assert p.parent == run and p.name == "x.png"
    p2 = c.download("https://cdn.rh.test/other/x.png", run)
    assert p2.parent == run and p2.name == "x_1.png"
    p3 = c.download("https://cdn.rh.test/out/..%5C..%5Cy.png", run)
    assert p3.parent == run and p3.name == "y.png"
    p4 = c.download("https://cdn.rh.test/", run)
    assert p4.parent == run and p4.name == "output.bin"


def test_wait_retries_transport_errors():
    fake = FakeRH(statuses=("RUNNING", "SUCCESS"))
    fails = {"n": 0}

    def flaky(req):
        if req.url.path == "/task/openapi/status" and fails["n"] < 2:
            fails["n"] += 1
            raise httpx.ConnectError("reset", request=req)
        return fake(req)

    c = RunningHubClient(BASE, "k", transport=httpx.MockTransport(flaky), poll_interval=0)
    assert c.wait("T1", cancel=threading.Event()) == "SUCCESS"


def test_wait_gives_up_after_transport_errors_and_cancels():
    seen = []

    def down(req):
        seen.append(req.url.path)
        if req.url.path == "/task/openapi/status":
            raise httpx.ConnectError("down", request=req)
        return httpx.Response(200, json={"code": 0})

    c = RunningHubClient(BASE, "k", transport=httpx.MockTransport(down), poll_interval=0)
    with pytest.raises(RunningHubError, match="连不上"):
        c.wait("T1", cancel=threading.Event())
    assert seen.count("/task/openapi/status") == 3 and seen[-1] == "/task/openapi/cancel"


def test_upload_returns_remote_file_name(tmp_path):
    fake = FakeRH()
    img = tmp_path / "首帧.png"
    img.write_bytes(b"xx")
    assert client(fake).upload(img) == "api/ref123.png"
    path, raw = fake.requests[0]
    assert path == "/task/openapi/upload" and b"sk-rh" in raw and b"xx" in raw


def test_http_error_is_chinese():
    c = RunningHubClient(BASE, "k", transport=httpx.MockTransport(lambda r: httpx.Response(502, text="bad")))
    with pytest.raises(RunningHubError, match="HTTP 502"):
        c.status("T1")


# ---------------------------------------------------------------- 引擎与注册表
MAP_IMG = """
params:
  prompt: [["6", "text"]]
  negative: [["7", "text"]]
  width: [["5", "width"]]
  height: [["5", "height"]]
  seed: [["3", "seed"]]
defaults:
  steps: [["3", "steps", 20]]
caps:
  max_refs: 0
  supports_negative: true
  sizes: {"9:16": [720, 1280], "16:9": [1280, 720]}
"""

MAP_VID = """
params:
  prompt: [["10", "text"]]
  first_frame: [["12", "image"]]
  frames: [["15", "length"]]
caps:
  fps: 16
  max_seconds: 5
  has_audio: false
  sizes: {"9:16": [480, 832]}
"""


def rh_cfg(tmp_path, **engines):
    for f in (ROOT / "workflows").iterdir():      # 本机引擎的模板也要在
        shutil.copy(f, tmp_path / f.name)
    (tmp_path / "runninghub_rh_img.map.yaml").write_text(MAP_IMG, encoding="utf-8")
    (tmp_path / "runninghub_rh_vid.map.yaml").write_text(MAP_VID, encoding="utf-8")
    eng = engines or {"rh_img": RunningHubEngineCfg(kind="image", workflow_id="wf-img", label="云端出图"),
                      "rh_vid": RunningHubEngineCfg(kind="video", webapp_id="app-vid")}
    return Config(workflows_dir=tmp_path, runninghub=RunningHubCfg(engines=eng))


def test_engines_absent_without_config():
    reg = EngineRegistry(Config())
    assert not any(runtime_of(e) == "runninghub" for e in reg.all().values())


def test_registry_loads_configured_engines(tmp_path):
    reg = EngineRegistry(rh_cfg(tmp_path))
    img, vid = reg.image("rh_img"), reg.video("rh_vid")
    assert runtime_of(img) == runtime_of(vid) == "runninghub"
    assert img.family == vid.family == "runninghub" and img.label == "云端出图"
    assert img.caps.supports_negative and img.caps.sizes["9:16"] == (720, 1280)
    assert vid.caps.fps == 16 and vid.frames_for(5) == 81


def test_disabled_engine_and_name_clash_is_skipped(tmp_path):
    reg = EngineRegistry(rh_cfg(tmp_path, rh_img=RunningHubEngineCfg(kind="image", workflow_id="w",
                                                                     enabled=False)))
    assert "rh_img" not in reg.names()
    (tmp_path / "runninghub_zimage.map.yaml").write_text(MAP_IMG, encoding="utf-8")
    reg = EngineRegistry(rh_cfg(tmp_path, zimage=RunningHubEngineCfg(kind="image", workflow_id="w")))
    assert runtime_of(reg.get("zimage")) == "comfyui"          # 本机引擎不被顶掉
    assert "重名" in reg.load_errors["zimage"]


def test_misconfigured_engine_does_not_crash_startup(tmp_path, monkeypatch):
    """配错的云端引擎（两个 id 都没写、映射文件不存在）跳过并记下原因，其他引擎照常；doctor 报失败。"""
    monkeypatch.setenv("RUNNINGHUB_API_KEY", "sk")
    cfg = rh_cfg(tmp_path, rh_img=RunningHubEngineCfg(kind="image"),
                 rh_missing=RunningHubEngineCfg(kind="image", workflow_id="w"),
                 rh_vid=RunningHubEngineCfg(kind="video", webapp_id="app"))
    reg = EngineRegistry(cfg)
    assert "rh_vid" in reg.names() and "rh_img" not in reg.names() and "rh_missing" not in reg.names()
    assert "workflow_id" in reg.load_errors["rh_img"]
    assert "映射文件不存在" in reg.load_errors["rh_missing"]
    checks = {c.name: c for c in check_runninghub(cfg, reg)}
    assert checks["rh_img"].status == "fail" and "workflow_id" in checks["rh_img"].detail
    assert checks["rh_missing"].status == "fail" and checks["rh_vid"].status == "ok"


def test_image_build_maps_node_info(tmp_path):
    call = EngineRegistry(rh_cfg(tmp_path)).image("rh_img").build(
        ImageRequest(prompt="a cat", negative="ugly", aspect="16:9", seed=5, refs=[tmp_path / "r.png"]))
    assert call.runner == "runninghub" and call.output_kind == "image"
    req = call.workflow
    assert req["workflow_id"] == "wf-img" and req["webapp_id"] is None
    nodes = {(n["nodeId"], n["fieldName"]): n["fieldValue"] for n in req["node_info_list"]}
    assert nodes == {("6", "text"): "a cat", ("7", "text"): "ugly", ("5", "width"): 1280,
                     ("5", "height"): 720, ("3", "seed"): 5, ("3", "steps"): 20}
    assert call.dropped_refs == [str(tmp_path / "r.png")]


def test_video_build_marks_first_frame_upload(tmp_path):
    ff = tmp_path / "ff.png"
    call = EngineRegistry(rh_cfg(tmp_path)).video("rh_vid").build(
        VideoRequest(prompt="walk", first_frame=ff, seconds=5))
    nodes = {(n["nodeId"], n["fieldName"]): n["fieldValue"] for n in call.workflow["node_info_list"]}
    assert nodes[("12", "image")] == UPLOAD_PREFIX + str(ff.resolve())
    assert nodes[("15", "length")] == 81 and call.workflow["webapp_id"] == "app-vid"


def test_check_ready_without_key(tmp_path, monkeypatch):
    monkeypatch.delenv("RUNNINGHUB_API_KEY", raising=False)
    reg = EngineRegistry(rh_cfg(tmp_path))
    with pytest.raises(JobError, match="RunningHub.*RUNNINGHUB_API_KEY"):
        check_ready(reg.image("rh_img"))
    check_ready(reg.image("zimage"))          # 本机引擎不受影响
    monkeypatch.setenv("RUNNINGHUB_API_KEY", "sk")
    check_ready(reg.image("rh_img"))


def test_doctor_warns_without_key(tmp_path, monkeypatch):
    monkeypatch.delenv("RUNNINGHUB_API_KEY", raising=False)
    cfg = rh_cfg(tmp_path)
    checks = check_runninghub(cfg, EngineRegistry(cfg))
    key = next(c for c in checks if c.name == "API key")
    assert key.status == "warn" and "RUNNINGHUB_API_KEY" in key.detail and "没有" in key.detail
    monkeypatch.setenv("RUNNINGHUB_API_KEY", "sk")
    checks = check_runninghub(cfg, EngineRegistry(cfg))
    assert next(c for c in checks if c.name == "API key").status == "ok"
    assert {c.name for c in checks} >= {"rh_img", "rh_vid"}
    assert check_runninghub(Config(), EngineRegistry(Config())) == []


# ---------------------------------------------------------------- runner
def test_runner_uploads_creates_waits_downloads(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNNINGHUB_API_KEY", "sk-rh")
    ff = tmp_path / "ff.png"
    ff.write_bytes(b"img")
    call = EngineRegistry(rh_cfg(tmp_path)).video("rh_vid").build(
        VideoRequest(prompt="walk", first_frame=ff, seconds=5))
    fake = FakeRH(outputs=[{"fileUrl": "https://cdn.rh.test/o/clip.mp4", "fileType": "mp4"}])
    logs = []
    runner = RunningHubRunner(client(fake))
    files = runner.run(call, run_dir=tmp_path / "run", cancel=threading.Event(), log=logs.append)
    assert [f.name for f in files] == ["clip.mp4"]
    create = next(b for p, b in fake.requests if p == "/task/openapi/ai-app/run")
    nodes = {(n["nodeId"], n["fieldName"]): n["fieldValue"] for n in create["nodeInfoList"]}
    assert nodes[("12", "image")] == "api/ref123.png"
    saved = json.loads((tmp_path / "run" / "workflow.api.json").read_text(encoding="utf-8"))
    assert saved["webapp_id"] == "app-vid"
    assert any("T1" in m for m in logs)


def test_runner_no_outputs_raises(tmp_path):
    call = EngineRegistry(rh_cfg(tmp_path)).image("rh_img").build(ImageRequest(prompt="p"))
    with pytest.raises(RunningHubError, match="没有产出"):
        RunningHubRunner(client(FakeRH(outputs=[]))).run(call, run_dir=tmp_path / "r",
                                                         cancel=threading.Event())


# ---------------------------------------------------------------- 接进流水线
def test_keyframe_runs_on_runninghub_runner(tmp_path, db, monkeypatch):
    from backend.config import ServerCfg
    from backend.domain.models import Shot
    from backend.pipeline.keyframes import run_keyframe
    from backend.tests.helpers import FakeCtx, FakeRunner, make_services, seed_episode

    monkeypatch.setenv("RUNNINGHUB_API_KEY", "sk")
    class FakeRHRunner(FakeRunner):
        def run(self, call, *, log=None, **kw):
            return super().run(call, **kw)

    (tmp_path / "wf").mkdir()
    cfg = rh_cfg(tmp_path / "wf")
    cfg.server = ServerCfg(data_dir=tmp_path / "data")
    sv = make_services(tmp_path, db, cfg=cfg, comfy=FakeRunner())
    sv.runninghub = FakeRHRunner()
    _, ep_id = seed_episode(db, image_engine="rh_img")
    with db.session() as s:
        sh = Shot(episode_id=ep_id, order=1, code="S01", image_prompt="a cat", duration=3)
        s.add(sh)
        s.flush()
        sid = sh.id
    res = run_keyframe(FakeCtx(sv), {"shot_id": sid})
    assert res["candidate_ids"] and sv.runninghub.calls[0].runner == "runninghub"
    assert sv.comfy.calls == []


def test_submit_chain_rejects_runninghub_without_key(tmp_path, db, monkeypatch):
    from backend.domain.models import Shot
    from backend.pipeline.runs import RunOptions, submit_chain
    from backend.tests.helpers import seed_episode

    monkeypatch.delenv("RUNNINGHUB_API_KEY", raising=False)
    (tmp_path / "wf").mkdir()
    reg = EngineRegistry(rh_cfg(tmp_path / "wf"))
    _, ep_id = seed_episode(db, image_engine="rh_img")
    with db.session() as s:
        s.add(Shot(episode_id=ep_id, order=1, code="S01", image_prompt="p", duration=3))

    class M:
        def submit(self, *a, **k):
            raise AssertionError("不该提交")

    with pytest.raises(JobError, match="RUNNINGHUB_API_KEY"):
        submit_chain(M(), db, reg, ep_id, ["keyframes"], RunOptions())
