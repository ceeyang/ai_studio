import threading

import pytest

from backend.engines.base import EngineCall, upload_ref
from backend.engines.comfyui.client import ComfyClient
from backend.engines.comfyui.errors import ComfyError
from backend.engines.comfyui.runner import ComfyRunner
from backend.queue.errors import JobCanceled


def runner_for(fake, **kw):
    return ComfyRunner(ComfyClient(fake.url, backoff=0, max_retries=2), poll_interval=0.05, **kw)


def make_call(wf=None):
    return EngineCall(engine="zimage",
                      workflow=wf or {"9": {"class_type": "SaveImage", "inputs": {}}},
                      output_kind="image", model_family="zimage", meta={"prompt": "p"})


def test_run_downloads_and_reports_progress(tmp_path, fake_comfy):
    fake_comfy.hold = True
    threading.Timer(0.5, fake_comfy.finish_all).start()
    progress = []
    files = runner_for(fake_comfy).run(make_call(), run_dir=tmp_path / "run",
                                       cancel=threading.Event(),
                                       on_progress=lambda v, m: progress.append((v, m)))
    assert [f.name for f in files] == ["out_00001_.png"]
    assert files[0].read_bytes() == b"fake-output"
    assert (tmp_path / "run" / "workflow.api.json").exists()
    assert (tmp_path / "run" / "call.json").exists()
    assert progress and progress[-1] == (3, 3)
    assert "view:studio/out_00001_.png" in fake_comfy.calls


def test_upload_placeholders_replaced(tmp_path, fake_comfy):
    img = tmp_path / "首帧.png"
    img.write_bytes(b"\x89PNG")
    wf = {"1": {"class_type": "LoadImage", "inputs": {"image": upload_ref(img)}},
          "2": {"class_type": "LoadImage", "inputs": {"image": upload_ref(img)}},
          "9": {"class_type": "SaveImage", "inputs": {}}}
    runner_for(fake_comfy).run(make_call(wf), run_dir=tmp_path / "run", cancel=threading.Event())
    sent = fake_comfy.last_workflow()
    name = sent["1"]["inputs"]["image"]
    assert name.startswith("studio_") and name == sent["2"]["inputs"]["image"]
    assert fake_comfy.uploads[name] == b"\x89PNG"
    assert [c for c in fake_comfy.calls if c.startswith("upload:")] == [f"upload:{name}"]


def test_execution_error_raises(tmp_path, fake_comfy):
    fake_comfy.exec_error = {"node_id": "12", "node_type": "VAEDecode",
                             "exception_message": "CUDA out of memory"}
    with pytest.raises(ComfyError) as ei:
        runner_for(fake_comfy).run(make_call(), run_dir=tmp_path / "r", cancel=threading.Event())
    assert ei.value.node_id == "12" and not ei.value.retryable


def test_rejected_workflow_reports_node(tmp_path, fake_comfy):
    fake_comfy.reject = {"error": {"message": "Prompt outputs failed validation"},
                         "node_errors": {"1": {"class_type": "UNETLoader", "errors": [
                             {"message": "Value not in list", "details": "unet_name: 'x' not in []"}]}}}
    with pytest.raises(ComfyError) as ei:
        runner_for(fake_comfy).run(make_call(), run_dir=tmp_path / "r", cancel=threading.Event())
    assert ei.value.node_type == "UNETLoader"


def test_cancel_interrupts_clears_and_frees(tmp_path, fake_comfy):
    fake_comfy.hold = True
    cancel = threading.Event()
    threading.Timer(0.3, cancel.set).start()
    with pytest.raises(JobCanceled):
        runner_for(fake_comfy).run(make_call(), run_dir=tmp_path / "r", cancel=cancel)
    assert {"interrupt", "clear_queue", "free"} <= set(fake_comfy.calls)


def test_timeout_interrupts(tmp_path, fake_comfy):
    fake_comfy.hold = True
    with pytest.raises(ComfyError, match="超过"):
        runner_for(fake_comfy).run(make_call(), run_dir=tmp_path / "r",
                                   cancel=threading.Event(), timeout=0.3)
    assert "interrupt" in fake_comfy.calls


def test_works_without_websocket(tmp_path, fake_comfy):
    files = runner_for(fake_comfy, use_ws=False).run(make_call(), run_dir=tmp_path / "r",
                                                     cancel=threading.Event())
    assert files[0].exists()


def test_video_outputs_downloaded(tmp_path, fake_comfy):
    fake_comfy.output_kind = "images"   # 新版 SaveVideo 也放在 images 下
    fake_comfy.output_name = "clip_00001_.mp4"
    files = runner_for(fake_comfy).run(make_call(), run_dir=tmp_path / "r", cancel=threading.Event())
    assert files[0].suffix == ".mp4"
