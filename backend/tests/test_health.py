"""GET /api/system/health：ComfyUI 和浏览器扩展连没连上；ComfyUI 检查 1 秒超时、连上缓存 15 秒、连不上缓存 2 秒。"""
from backend.engines.comfyui import health
from backend.engines.comfyui.errors import ComfyError


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
    assert body["comfyui"] == {"ok": False, "message": "连不上 X"}


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
