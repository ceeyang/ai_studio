import httpx
import pytest

from backend.engines.comfyui.client import ComfyClient
from backend.engines.comfyui.errors import ComfyError, execution_error


def make(handler, **kw):
    return ComfyClient("http://comfy.test", transport=httpx.MockTransport(handler), backoff=0, **kw)


def test_retries_connection_errors_then_succeeds():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ConnectError("refused", request=req)
        return httpx.Response(200, json={"devices": []})

    assert make(handler).system_stats() == {"devices": []}
    assert calls["n"] == 3


def test_gives_up_as_retryable_after_max_retries():
    c = make(lambda req: httpx.Response(503, text="busy"), max_retries=2)
    with pytest.raises(ComfyError) as ei:
        c.system_stats()
    assert ei.value.retryable is True
    assert "重试 2 次" in str(ei.value)


def test_node_error_is_not_retried():
    calls = {"n": 0}
    body = {"error": {"type": "prompt_outputs_failed_validation",
                      "message": "Prompt outputs failed validation", "details": ""},
            "node_errors": {"1": {"class_type": "UNETLoader", "errors": [
                {"type": "value_not_in_list", "message": "Value not in list",
                 "details": "unet_name: 'x.safetensors' not in []"}]}}}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(400, json=body)

    with pytest.raises(ComfyError) as ei:
        make(handler).queue_prompt({"1": {}})
    e = ei.value
    assert calls["n"] == 1
    assert e.retryable is False and e.node_id == "1" and e.node_type == "UNETLoader"
    assert "x.safetensors" in str(e)


def test_upload_dedupes_and_names_by_content(tmp_path):
    bodies = []

    def handler(req):
        bodies.append(req.read().decode("latin-1"))
        return httpx.Response(200, json={"name": "studio_x.png", "subfolder": "sub", "type": "input"})

    img = tmp_path / "参考 图.PNG"
    img.write_bytes(b"\x89PNGdata")
    c = make(handler)
    assert c.upload_image(img) == "sub/studio_x.png"
    assert c.upload_image(img) == "sub/studio_x.png"
    assert len(bodies) == 1
    assert 'filename="studio_' in bodies[0] and '.png"' in bodies[0]


def test_history_missing_returns_none():
    c = make(lambda req: httpx.Response(200, json={}))
    assert c.history("abc") is None


def test_ws_url():
    c = ComfyClient("http://127.0.0.1:8188/")
    assert c.ws_url == f"ws://127.0.0.1:8188/ws?clientId={c.client_id}"


def test_execution_error_parsing():
    entry = {"status": {"status_str": "error", "messages": [
        ["execution_start", {}],
        ["execution_error", {"node_id": "12", "node_type": "VAEDecode",
                             "exception_message": "CUDA out of memory\n"}]]}}
    e = execution_error(entry)
    assert e.node_id == "12" and "CUDA out of memory" in str(e) and not e.retryable
    assert execution_error({"status": {"status_str": "success", "messages": []}}) is None
    assert execution_error({"status": {"status_str": "error", "messages": []}}) is not None
