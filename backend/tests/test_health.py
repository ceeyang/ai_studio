"""GET /api/system/health：ComfyUI 和浏览器扩展连没连上；ComfyUI 检查 1 秒超时、连上缓存 15 秒、连不上缓存 2 秒。"""
from backend.engines.comfyui import health
from backend.engines.comfyui.client import ComfyClient
from backend.engines.comfyui.errors import ComfyError
import httpx
import pytest


class Client:
    base_url = "http://127.0.0.1:8188"

    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    def system_stats(self, *, tries=None, timeout=None):
        self.calls.append((tries, timeout))
        if self.fail:
            raise ComfyError("连不上 ComfyUI")
        return {"devices": [{"name": "RTX"}]}


def test_health_endpoint_shape(api):
    r = api.get("/api/system/health")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"comfyui", "browser_ext"}
    for k in body:
        assert isinstance(body[k]["ok"], bool) and isinstance(body[k]["message"], str)
    assert body["comfyui"]["ok"] is True   # conftest 默认当 ComfyUI 在线


def test_health_endpoint_comfy_down(api, monkeypatch):
    monkeypatch.setattr(health, "check_comfy", lambda client: health.ComfyHealth(False, "连不上 X"))
    body = api.get("/api/system/health").json()
    assert body["comfyui"]["ok"] is False and body["comfyui"]["message"] == "连不上 X"


def test_check_comfy_timeout_and_cache():
    health.reset_cache()
    now = [100.0]
    c = Client()
    h = health.check_comfy(c, clock=lambda: now[0])
    assert h.ok and "127.0.0.1:8188" in h.message
    assert c.calls == [(1, 1.0)]
    now[0] += 10
    assert health.check_comfy(c, clock=lambda: now[0]).ok and len(c.calls) == 1   # 15 秒内用缓存
    now[0] += 6
    c.fail = True
    h = health.check_comfy(c, clock=lambda: now[0])
    assert not h.ok and "127.0.0.1:8188" in h.message and len(c.calls) == 2
    assert not health.check_comfy(None).ok
    health.reset_cache()


def test_check_comfy_failure_cached_briefly():
    """连不上只缓存 2 秒：用户刚启动 ComfyUI 就能提交，不用等 15 秒。"""
    health.reset_cache()
    now = [200.0]
    c = Client(fail=True)
    assert not health.check_comfy(c, clock=lambda: now[0]).ok
    now[0] += 1
    assert not health.check_comfy(c, clock=lambda: now[0]).ok and len(c.calls) == 1   # 2 秒内用缓存
    now[0] += 1.5
    c.fail = False
    assert health.check_comfy(c, clock=lambda: now[0]).ok and len(c.calls) == 2
    health.reset_cache()


def _client_raising(exc: Exception, base_url: str = "http://127.0.0.1:8188") -> ComfyClient:
    def handler(request):
        raise exc
    return ComfyClient(base_url, transport=httpx.MockTransport(handler), backoff=0)


@pytest.mark.parametrize("exc, reason", [
    (httpx.ConnectTimeout("timed out"), "超时"),
    (httpx.ReadTimeout("timed out"), "超时"),
    (httpx.ConnectError("[WinError 10061] No connection could be made"), "拒绝连接"),
    (httpx.ConnectError("[Errno 11001] getaddrinfo failed"), "地址不对"),
])
def test_check_comfy_short_chinese_message_with_raw_detail(exc, reason):
    """横幅里只放一句短中文（连不上 主机:端口（原因）），英文原文放 detail。"""
    health.reset_cache()
    h = health.check_comfy(_client_raising(exc))
    assert not h.ok
    assert h.message == f"连不上 127.0.0.1:8188（{reason}）"
    assert str(exc) in h.detail
    health.reset_cache()


def test_check_comfy_server_error_and_bad_url():
    health.reset_cache()
    c = ComfyClient("http://127.0.0.1:8188", backoff=0,
                    transport=httpx.MockTransport(lambda r: httpx.Response(502, text="bad gateway")))
    h = health.check_comfy(c)
    assert h.message == "连不上 127.0.0.1:8188（ComfyUI 出错 HTTP 502）" and "bad gateway" in h.detail
    health.reset_cache()
    h = health.check_comfy(_client_raising(httpx.UnsupportedProtocol("Request URL is missing a scheme"),
                                           base_url="localhost:8188"))
    assert h.message == "连不上 localhost:8188（地址不对）"
    health.reset_cache()


def test_health_endpoint_returns_detail(api, monkeypatch):
    monkeypatch.setattr(health, "check_comfy",
                        lambda client: health.ComfyHealth(False, "连不上 X（超时）", "ConnectTimeout: timed out"))
    body = api.get("/api/system/health").json()
    assert body["comfyui"] == {"ok": False, "message": "连不上 X（超时）", "detail": "ConnectTimeout: timed out"}
