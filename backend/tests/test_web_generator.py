"""即梦 / 可灵网页出视频（生成器页，只走浏览器扩展）：引擎 build() 的请求、区域设置、扩展太老的提示、扩展桥往返。"""
from __future__ import annotations

import threading

from backend.engines.base import VideoRequest
from backend.engines.browser.engines import JimengVideoEngine, KlingWebVideoEngine
from backend.engines.browser.errors import BrowserError
from backend.engines.browser.sites.generators import GENERATOR_SITES, generator_site
from backend.tests.test_browser_extension import EXT, hello, send_image
from backend.tests.test_web_media import _mp4

V4 = {"version": "0.4.1", "sites": ["chatgpt", "gemini", "muse"], "features": ["rpc", "conversation", "video"]}
V5 = {"version": "0.5.0", "sites": ["chatgpt", "gemini", "muse", *GENERATOR_SITES],
      "features": ["rpc", "conversation", "video", "generator"]}


# ---------------------------------------------------------------- 引擎
def test_jimeng_build_request_with_end_frame(tmp_path):
    ff, ef = tmp_path / "first.png", tmp_path / "end.png"
    call = JimengVideoEngine().build(VideoRequest(prompt="女孩回头\n微笑", first_frame=ff, end_frame=ef, seconds=6.0,
                                                  refs=[tmp_path / "chen.png"]))
    w = call.workflow
    assert call.runner == "browser" and call.output_kind == "video" and call.model_family == "jimeng_web"
    assert w["generator"] is True and w["site"] == "jimeng_cn" and w["kind"] == "video"
    assert w["refs"] == [str(ff), str(ef)] and w["prompt"] == "女孩回头 微笑" and w["fit_aspect"] is False
    # 6 秒的镜头取不比它短的最近一档：10 秒
    assert w["choices"] == {"model": "Seedance 2.0 mini", "mode": None, "ratio": "9:16", "duration": 10,
                            "end_frame": True}
    assert call.meta["seconds"] == 10.0 and call.meta["frames"] == 240 and call.meta["region"] == "cn"
    assert call.meta["end_frame"] == str(ef) and call.dropped_refs == [str(tmp_path / "chen.png")]
    c = JimengVideoEngine.caps
    assert c.supports_end_frame and c.has_audio and c.duration_control == "param"
    assert c.allowed_seconds == (5.0, 10.0, 15.0) and c.max_seconds == 15.0
    # 比最长一档还长：取 15 秒；没给尾帧：不传
    call = JimengVideoEngine().build(VideoRequest(prompt="x", first_frame=ff, seconds=20.0, aspect="16:9"))
    assert call.workflow["choices"]["duration"] == 15 and call.workflow["choices"]["end_frame"] is False
    assert call.workflow["refs"] == [str(ff)] and call.workflow["choices"]["ratio"] == "16:9"


def test_kling_build_drops_end_frame_and_params_override(tmp_path):
    ff, ef = tmp_path / "first.png", tmp_path / "end.png"
    e = KlingWebVideoEngine({"model": "可灵 3.0", "mode": "图生视频", "timeout": 600})
    call = e.build(VideoRequest(prompt="x", first_frame=ff, end_frame=ef, seconds=4.0))
    w = call.workflow
    assert w["site"] == "kling_cn" and w["refs"] == [str(ff)] and w["timeout"] == 600.0
    assert w["choices"] == {"model": "可灵 3.0", "mode": "图生视频", "ratio": "9:16", "duration": 5, "end_frame": False}
    assert call.dropped_refs == [str(ef)]   # 可灵网页还不支持尾帧
    assert KlingWebVideoEngine.caps.allowed_seconds == (5.0, 10.0) and not KlingWebVideoEngine.caps.supports_end_frame
    assert KlingWebVideoEngine().build(VideoRequest(prompt="x", first_frame=ff, seconds=7.5)).workflow["choices"][
        "duration"] == 10
    # 模型不写：不动页面上现在选的
    assert KlingWebVideoEngine().build(VideoRequest(prompt="x", first_frame=ff)).workflow["choices"]["model"] is None


def test_region_source_and_params_region(tmp_path):
    ff = tmp_path / "f.png"
    e = JimengVideoEngine()
    e.set_region_source(lambda fam: "global" if fam == "jimeng" else "cn")
    assert e.build(VideoRequest(prompt="x", first_frame=ff)).workflow["site"] == "jimeng_global"
    e.set_region_source(lambda fam: "mars")   # 不认识的区域：国内
    assert e.build(VideoRequest(prompt="x", first_frame=ff)).workflow["site"] == "jimeng_cn"
    k = KlingWebVideoEngine({"region": "global"})
    k.set_region_source(lambda fam: "cn")     # params.region 写死时不看设置
    assert k.build(VideoRequest(prompt="x", first_frame=ff)).meta["region"] == "global"
    assert generator_site("kling", "global").generate_url == "https://kling.ai/app/omni/new"
    assert generator_site("jimeng", None).key == "jimeng_cn"


# ---------------------------------------------------------------- 设置页的区域
def test_settings_region_roundtrip_changes_engine_site(api, tmp_path):
    d = api.get("/api/settings").json()
    assert d["web_region_jimeng"] == "cn" and d["web_region_kling"] == "cn"
    d = api.put("/api/settings", json={"web_region_jimeng": "global"}).json()
    assert d["web_region_jimeng"] == "global" and d["web_region_kling"] == "cn"
    eng = api.app.state.services.engines.video("jimeng_video")
    assert eng.build(VideoRequest(prompt="x", first_frame=tmp_path / "f.png")).workflow["site"] == "jimeng_global"
    assert api.put("/api/settings", json={"web_region_kling": "mars"}).status_code == 422
    d = api.put("/api/settings", json={"web_region_jimeng": None}).json()
    assert d["web_region_jimeng"] == "cn"
    assert eng.build(VideoRequest(prompt="x", first_frame=tmp_path / "f.png")).workflow["site"] == "jimeng_cn"


# ---------------------------------------------------------------- 引擎下拉：扩展太老
def _video_rows(api):
    return {r["name"]: r for r in api.get("/api/engines").json()["video"]}


def test_engine_rows_hint_reload_for_old_extension(api):
    rows = _video_rows(api)
    assert rows["jimeng_video"]["ready"] is False and "没有连上" in rows["jimeng_video"]["note"]
    assert rows["jimeng_video"]["hint"] is None
    with api.websocket_connect("/api/browser/ext", headers=EXT) as ws:
        hello(ws, **V4)
        rows = _video_rows(api)
        assert rows["gemini_video"]["ready"] is True
        for name in ("jimeng_video", "kling_web_video"):
            assert rows[name]["ready"] is False and rows[name]["hint"] == "扩展需要重新加载"
            assert "v0.4.1" in rows[name]["note"] and "即梦 / 可灵" in rows[name]["note"]
        st = api.get("/api/browser/status").json()
        assert st["needs_reload"] is True and st["missing_features"] == ["generator"]
        choices = {c["name"]: c for c in api.get("/api/settings").json()["choices"]["video"]}
        assert choices["kling_web_video"]["hint"] == "扩展需要重新加载"
    with api.websocket_connect("/api/browser/ext", headers=EXT) as ws:
        hello(ws, **V5)
        rows = _video_rows(api)
        assert rows["jimeng_video"]["ready"] is True and rows["jimeng_video"]["note"] is None
        assert rows["jimeng_video"]["label"] == "即梦网页（Seedance）"


def test_doctor_reports_generator_sites_and_old_extension(api):
    api.put("/api/settings", json={"web_region_kling": "global"})
    with api.websocket_connect("/api/browser/ext", headers=EXT) as ws:
        hello(ws, **V4)
        checks = {c["name"]: c for c in api.get("/api/doctor").json() if c["group"] == "浏览器"}
        assert checks["扩展版本"]["status"] == "warn" and "即梦 / 可灵" in checks["扩展版本"]["detail"]
        # 按区域取站点：可灵设成了海外
        assert "即梦 登录（jimeng_video）" in checks and "Kling 登录（kling_web_video）" in checks
        assert "kling.ai" in checks["Kling 登录（kling_web_video）"]["detail"]


# ---------------------------------------------------------------- 扩展桥往返
class GenJob:
    def __init__(self, api, tmp_path, engine="jimeng_video", end=False):
        sv = api.app.state.services
        self.runner = sv.browser
        ff = tmp_path / "first.png"
        ff.write_bytes(b"\x89PNG fake")
        ef = tmp_path / "end.png"
        ef.write_bytes(b"\x89PNG fake2")
        self.call = sv.engines.video(engine).build(VideoRequest(prompt="灯笼在雨里摇", first_frame=ff, seconds=5,
                                                                end_frame=ef if end else None))
        self.run_dir = tmp_path / "run"
        self.logs = []
        self.files = self.error = None
        self.t = threading.Thread(target=self._go, daemon=True)
        self.t.start()

    def _go(self):
        try:
            self.files = self.runner.run(self.call, run_dir=self.run_dir, cancel=threading.Event(),
                                         log=self.logs.append)
        except BaseException as e:   # noqa: BLE001
            self.error = e

    def join(self):
        self.t.join(10)
        assert not self.t.is_alive()
        return self


def test_generator_job_roundtrip_saves_mp4(api, tmp_path):
    data = _mp4(tmp_path / "src.mp4")
    with api.websocket_connect("/api/browser/ext", headers=EXT) as ws:
        hello(ws, **V5)
        job = GenJob(api, tmp_path, end=True)
        msg = ws.receive_json()
        assert msg["type"] == "job" and msg["kind"] == "video" and msg["site"] == "jimeng_cn"
        assert msg["generator"] == {"model": "Seedance 2.0 mini", "mode": None, "ratio": "9:16", "duration": 5,
                                    "end_frame": True}
        assert [r["name"] for r in msg["refs"]] == ["first.png", "end.png"] and msg["timeout"] == 1200
        assert "conversation_url" not in msg and msg["prompt"] == "灯笼在雨里摇"
        send_image(ws, msg["job_id"], data, chunk=4000, mime="video/mp4", src="https://v9-artist.vlabvod.com/x.mp4")
        ws.send_json({"type": "result", "job_id": msg["job_id"], "count": 1, "conversation": None})
        job.join()
    assert job.error is None, job.error
    assert [f.name for f in job.files] == ["jimeng_cn_00.mp4"] and job.files[0].read_bytes() == data
    assert (job.run_dir / "workflow.api.json").exists() and (job.run_dir / "call.json").exists()
    assert any("即梦：视频 72×128" in m for m in job.logs)


def test_generator_job_on_old_extension_asks_reload(api, tmp_path):
    with api.websocket_connect("/api/browser/ext", headers=EXT) as ws:
        hello(ws, **V4)
        job = GenJob(api, tmp_path, engine="kling_web_video").join()
    assert isinstance(job.error, BrowserError) and "重新加载" in str(job.error) and "即梦 / 可灵" in str(job.error)


def test_generator_job_error_from_extension(api, tmp_path):
    with api.websocket_connect("/api/browser/ext", headers=EXT) as ws:
        hello(ws, **V5)
        job = GenJob(api, tmp_path, engine="kling_web_video")
        msg = ws.receive_json()
        assert msg["site"] == "kling_cn" and msg["generator"]["end_frame"] is False
        ws.send_json({"type": "error", "job_id": msg["job_id"], "kind": "quota", "message": "可灵 积分不够：余额 0"})
        job.join()
    assert isinstance(job.error, BrowserError) and job.error.kind == "quota"


def test_cdp_transport_rejects_generator(api, tmp_path, monkeypatch):
    monkeypatch.setattr(api.app.state.services.browser.session.cfg, "transport", "cdp")
    job = GenJob(api, tmp_path).join()
    assert isinstance(job.error, BrowserError) and "只支持浏览器扩展" in str(job.error)
