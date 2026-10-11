"""扩展桥（transport=extension）：/api/browser/ext 的来源检查、协议、任务收发、分块传图、出错/断开/取消、
状态接口和 doctor。用 TestClient 的 websocket 当假的扩展。"""
import base64
import threading
import time

import pytest
from PIL import Image
from starlette.websockets import WebSocketDisconnect

from backend.app.routes.browser_ext import extension_origin_ok
from backend.doctor import check_browser
from backend.engines.base import ImageRequest
from backend.engines.browser.errors import BrowserError
from backend.engines.browser.extension import ExtensionHub, ExtImage, decode
from backend.queue.errors import JobCanceled
from backend.tests.fakes.fake_browser import png

EXT = {"origin": "chrome-extension://abcdefghijklmnop"}
GPT_SRC = "https://chatgpt.com/backend-api/estuary/content?id=file_final"


def hello(ws, **kw):
    ws.send_json({"type": "hello", "version": "0.1.0", "sites": ["chatgpt", "gemini"],
                  "browser": "Chrome 141", **kw})
    welcome = ws.receive_json()
    assert welcome["type"] == "welcome"
    return welcome


def wait_for(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


class Job:
    """在线程里跑 runner.run（网页通道线程那样），测试线程扮演扩展。"""

    def __init__(self, api, engine="gpt_image", aspect="9:16", refs=(), tmp_path=None):
        sv = api.app.state.services
        self.runner = sv.browser
        self.call = sv.engines.image(engine).build(ImageRequest(prompt="陈师傅 in a workshop", aspect=aspect,
                                                                refs=list(refs)))
        self.run_dir = tmp_path / "run"
        self.cancel = threading.Event()
        self.logs, self.progress = [], []
        self.files = self.error = None
        self.t = threading.Thread(target=self._go, daemon=True)
        self.t.start()

    def _go(self):
        try:
            self.files = self.runner.run(self.call, run_dir=self.run_dir, cancel=self.cancel,
                                         on_progress=lambda v, m: self.progress.append((v, m)),
                                         log=self.logs.append)
        except BaseException as e:   # noqa: BLE001 — 测试里要把任何异常交回主线程看
            self.error = e

    def join(self):
        self.t.join(10)
        assert not self.t.is_alive()
        return self


def send_image(ws, job_id, data: bytes, *, chunk=1000, mime="image/png", src=GPT_SRC, index=0):
    b64 = base64.b64encode(data).decode()
    parts = [b64[i:i + chunk] for i in range(0, len(b64), chunk)]
    for seq, part in enumerate(parts):
        ws.send_json({"type": "image", "job_id": job_id, "index": index, "seq": seq, "total": len(parts),
                      "mime": mime, "src": src, "via": "page", "data": part})


# ---------------------------------------------------------------- 来源
@pytest.mark.parametrize("origin", [None, "http://localhost:5173", "http://127.0.0.1:8700", "null",
                                    "https://evil.example", "chrome-extension://", "moz-extension://abc",
                                    "chrome-extension://abc/../x"])
def test_ext_endpoint_rejects_non_extension_origin(api, origin):
    headers = {"origin": origin} if origin else {}
    with pytest.raises(WebSocketDisconnect) as exc:
        with api.websocket_connect("/api/browser/ext", headers=headers) as ws:
            ws.receive_json()
    assert exc.value.code == 1008


def test_ext_origin_rules():
    assert extension_origin_ok("chrome-extension://abcdefghijklmnop")
    assert extension_origin_ok("extension://abcdefghijklmnop/")
    assert extension_origin_ok("chrome-extension://abc", "ABC")
    assert not extension_origin_ok("chrome-extension://abc", "other")
    assert not extension_origin_ok("http://localhost")


def test_pinned_extension_id(api, monkeypatch):
    monkeypatch.setattr(api.app.state.cfg.browser, "extension_id", "pinnedid")
    with pytest.raises(WebSocketDisconnect):
        with api.websocket_connect("/api/browser/ext", headers=EXT) as ws:
            ws.receive_json()
    with api.websocket_connect("/api/browser/ext", headers={"origin": "chrome-extension://pinnedid"}) as ws:
        hello(ws)


def test_events_ws_still_rejects_extension_origin(api):
    with pytest.raises(WebSocketDisconnect) as exc:
        with api.websocket_connect("/api/ws", headers=EXT) as ws:
            ws.receive_json()
    assert exc.value.code == 1008


# ---------------------------------------------------------------- 连接、ping、状态
def test_hello_ping_status_and_doctor(api):
    hub = api.app.state.services.browser.hub
    st = api.get("/api/browser/status").json()
    assert st["transport"] == "extension" and st["connected"] is False
    assert st["logged_in"] == {"chatgpt": None, "gemini": None, "muse": None, "claude": None}
    assert st["extension_dir"].replace("\\", "/").endswith("/browser-extension")
    rows = {c["name"]: c for c in api.get("/api/doctor").json() if c["group"] == "浏览器"}
    assert rows["扩展连接"]["status"] == "warn" and "加载已解压的扩展程序" in rows["扩展连接"]["detail"]
    assert "在装了扩展的浏览器里登录 gemini.google.com / chatgpt.com" in rows["ChatGPT 登录（gpt_image）"]["detail"]

    with api.websocket_connect("/api/browser/ext", headers=EXT) as ws:
        w = hello(ws)
        assert w["options"] == {"reuse_conversation": False, "reuse_user_tabs": True, "activate_tab": False}
        ws.send_json({"type": "ping", "t": 1})
        assert ws.receive_json() == {"type": "pong", "t": 1}
        ws.send_json({"type": "status", "sites": {
            "chatgpt": {"tabs": 2, "logged_in": True, "cookie": True, "page": "logged_in"},
            "gemini": {"tabs": 0, "logged_in": False, "cookie": False, "page": None}}})
        assert wait_for(lambda: hub.site_status().get("gemini") is not None)
        st = api.get("/api/browser/status").json()       # 刚报过：不催扩展
        hub._status_at -= 60
        api.get("/api/browser/status")
        assert ws.receive_json() == {"type": "status_request"}     # 状态旧了，查状态时让扩展再报一次
        assert st["connected"] and st["extension"]["version"] == "0.1.0"
        assert st["extension"]["ext_id"] == "abcdefghijklmnop" and st["extension"]["browser"] == "Chrome 141"
        assert st["logged_in"] == {"chatgpt": True, "gemini": False, "muse": None, "claude": None}
        assert st["sites"]["chatgpt"]["tabs"] == 2
        rows = {c.name: c for c in check_browser(api.app.state.services.engines, api.app.state.services.browser)}
        assert rows["扩展连接"].status == "ok" and "v0.1.0" in rows["扩展连接"].detail
        assert rows["ChatGPT 登录（gpt_image）"].status == "ok" and "开着 2 个标签页" in rows["ChatGPT 登录（gpt_image）"].detail
        g = rows["Gemini 登录（gemini_image、gemini_video）"]
        assert g.status == "warn" and "在装了扩展的浏览器里登录" in g.detail
    assert wait_for(lambda: not hub.connected)
    assert api.get("/api/browser/status").json()["connected"] is False


def test_login_button_asks_extension_to_open_sites(api):
    r = api.post("/api/browser/login")
    assert r.status_code == 400 and "加载已解压的扩展程序" in r.json()["detail"]
    with api.websocket_connect("/api/browser/ext", headers=EXT) as ws:
        hello(ws)
        r = api.post("/api/browser/login")
        assert r.status_code == 200 and r.json()["transport"] == "extension" and r.json()["launched"] is False
        assert ws.receive_json() == {"type": "open", "urls": ["https://chatgpt.com/", "https://gemini.google.com/app",
                                                              "https://muse.ai/", "https://claude.ai/"]}


def test_new_connection_replaces_old(api):
    hub = api.app.state.services.browser.hub
    with api.websocket_connect("/api/browser/ext", headers=EXT) as old:
        hello(old, version="0.0.9")
        with api.websocket_connect("/api/browser/ext", headers=EXT) as new:
            hello(new, version="0.1.0")
            with pytest.raises(WebSocketDisconnect) as exc:
                old.receive_json()
            assert exc.value.code == 4000
            assert hub.info()["version"] == "0.1.0"


# ---------------------------------------------------------------- 出图
def test_job_roundtrip_chunked_image_saved_and_cropped(api, tmp_path):
    ref = tmp_path / "chen.png"
    ref.write_bytes(png(64, 64))
    with api.websocket_connect("/api/browser/ext", headers=EXT) as ws:
        hello(ws)
        job = Job(api, refs=[ref], tmp_path=tmp_path)
        msg = ws.receive_json()
        assert msg["type"] == "job" and msg["site"] == "chatgpt" and msg["aspect"] == "9:16"
        assert msg["timeout"] == 420 and "陈师傅 in a workshop" in msg["prompt"] and "\n" not in msg["prompt"]
        assert msg["refs"] == [{"name": "chen.png", "mime": "image/png",
                                "b64": base64.b64encode(ref.read_bytes()).decode()}]
        assert msg["options"]["reuse_conversation"] is False
        jid = msg["job_id"]
        ws.send_json({"type": "progress", "job_id": jid, "value": 40, "max": 100})
        ws.send_json({"type": "log", "job_id": jid, "message": "ChatGPT：已提交，等出图"})
        ws.send_json({"type": "log", "job_id": "someone-else", "message": "不该出现"})
        send_image(ws, jid, png(1024, 1536), chunk=700)
        ws.send_json({"type": "result", "job_id": jid, "count": 1})
        job.join()
    assert job.error is None, job.error
    assert [f.name for f in job.files] == ["chatgpt_00.png"]
    assert Image.open(job.files[0]).size == (864, 1536)
    assert (tmp_path / "run" / "chatgpt_00_orig.png").exists()
    assert (40, 100) in job.progress
    assert any("任务已发给浏览器扩展（v0.1.0）" in m for m in job.logs)
    assert any("已提交，等出图" in m for m in job.logs) and not any("不该出现" in m for m in job.logs)
    assert any("原图 1024×1536 PNG" in m for m in job.logs)


def test_job_two_images_kept(api, tmp_path):
    with api.websocket_connect("/api/browser/ext", headers=EXT) as ws:
        hello(ws)
        job = Job(api, engine="gemini_image", aspect="1:1", tmp_path=tmp_path)
        jid = ws.receive_json()["job_id"]
        send_image(ws, jid, png(1024, 1024), index=0, src="https://lh3.googleusercontent.com/a")
        send_image(ws, jid, png(1024, 1024, (1, 2, 3)), index=1, src="https://lh3.googleusercontent.com/b")
        ws.send_json({"type": "result", "job_id": jid, "count": 2})
        job.join()
    assert job.error is None and [f.name for f in job.files] == ["gemini_00_orig.png", "gemini_01_orig.png"]


@pytest.mark.parametrize("kind", ["not_logged_in", "quota", "refused", "timeout", "no_image", "page"])
def test_job_error_from_extension(api, tmp_path, kind):
    with api.websocket_connect("/api/browser/ext", headers=EXT) as ws:
        hello(ws)
        job = Job(api, tmp_path=tmp_path)
        jid = ws.receive_json()["job_id"]
        ws.send_json({"type": "error", "job_id": jid, "kind": kind, "message": f"ChatGPT 出错了：{kind}"})
        job.join()
    assert isinstance(job.error, BrowserError) and job.error.kind == kind
    assert str(job.error) == f"ChatGPT 出错了：{kind}"


def test_unknown_error_kind_becomes_browser(api, tmp_path):
    with api.websocket_connect("/api/browser/ext", headers=EXT) as ws:
        hello(ws)
        job = Job(api, tmp_path=tmp_path)
        jid = ws.receive_json()["job_id"]
        ws.send_json({"type": "error", "job_id": jid, "kind": "weird", "message": "x"})
        job.join()
    assert job.error.kind == "browser"


def test_extension_returns_garbage_image(api, tmp_path):
    with api.websocket_connect("/api/browser/ext", headers=EXT) as ws:
        hello(ws)
        job = Job(api, tmp_path=tmp_path)
        jid = ws.receive_json()["job_id"]
        send_image(ws, jid, b"<html>not an image</html>", mime="text/html")
        ws.send_json({"type": "result", "job_id": jid, "count": 1})
        job.join()
    assert isinstance(job.error, BrowserError) and "不是能解开的图片" in str(job.error)


def test_disconnect_mid_job_fails_fast(api, tmp_path):
    with api.websocket_connect("/api/browser/ext", headers=EXT) as ws:
        hello(ws)
        job = Job(api, tmp_path=tmp_path)
        assert ws.receive_json()["type"] == "job"
    job.join()
    assert isinstance(job.error, BrowserError) and "扩展连接断开" in str(job.error)


def test_cancel_sends_cancel_to_extension(api, tmp_path):
    with api.websocket_connect("/api/browser/ext", headers=EXT) as ws:
        hello(ws)
        job = Job(api, tmp_path=tmp_path)
        jid = ws.receive_json()["job_id"]
        job.cancel.set()
        assert ws.receive_json() == {"type": "cancel", "job_id": jid}
        job.join()
    assert isinstance(job.error, JobCanceled)


def test_not_connected_job_fails_with_install_hint(api, tmp_path):
    job = Job(api, tmp_path=tmp_path).join()
    assert isinstance(job.error, BrowserError) and job.error.kind == "browser"
    assert "加载已解压的扩展程序" in str(job.error) and "browser-extension" in str(job.error)


# ---------------------------------------------------------------- 纯逻辑
def test_assemble_incomplete_chunks():
    chunks = {}
    ExtensionHub._add_chunk(chunks, {"index": 0, "seq": 0, "total": 2, "data": "aGVs"})
    with pytest.raises(BrowserError, match="1/2 块"):
        ExtensionHub._assemble(chunks, 1, "ChatGPT")
    with pytest.raises(BrowserError, match="没有传回图片"):
        ExtensionHub._assemble({}, 0, "ChatGPT")


def test_hub_timeout_without_answer(monkeypatch):
    import asyncio

    hub = ExtensionHub()

    class Sock:
        async def send_json(self, msg):
            pass

    loop = asyncio.new_event_loop()
    t = threading.Thread(target=loop.run_forever, daemon=True)
    t.start()
    try:
        from backend.engines.browser.extension import _Conn

        hub._conn = _Conn(Sock(), loop, "chrome-extension://x")
        now = [0.0]

        def clock():
            now[0] += 100
            return now[0]
        with pytest.raises(BrowserError, match="还没回结果") as exc:
            hub.generate("chatgpt", "ChatGPT", "p", [], aspect="9:16", timeout=60,
                         cancel=threading.Event(), log=lambda m: None, clock=clock, poll=0.01)
        assert exc.value.kind == "timeout"
    finally:
        loop.call_soon_threadsafe(loop.stop)
        t.join(2)


def test_decode_reports_size_and_format():
    assert decode(ExtImage(png(30, 20), "image/png", "s", "page"), "X") == (30, 20, "PNG")


def test_ready_conn_waits_for_a_quick_reconnect():
    """扩展刚断开（重新加载）时等它重连上、报完 hello 再发；从没连上过的马上返回 None。"""
    import threading
    import time

    from backend.engines.browser.extension import ExtensionHub

    hub = ExtensionHub()
    assert hub.ready_conn(grace=5) is None            # 从没连上过：不等

    class C:
        hello = {"version": "0.5.0", "features": []}

    hub.last_disconnect = time.time()                  # 刚断开
    conn = C()
    threading.Timer(0.3, lambda: setattr(hub, "_conn", conn)).start()
    t0 = time.monotonic()
    assert hub.ready_conn(grace=5, poll=0.05) is conn
    assert time.monotonic() - t0 < 3

    hub2 = ExtensionHub()
    hub2.last_disconnect = time.time() - 120           # 断了很久：不等
    t0 = time.monotonic()
    assert hub2.ready_conn(grace=5) is None
    assert time.monotonic() - t0 < 1
