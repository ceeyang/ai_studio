"""网页出视频（Gemini / Muse）和 Muse 出图（docs/specs/2026-10-11-web-media-design.md）：出视频的引擎请求和时长、
扩展桥收发视频任务、视频落盘校验、出视频接上游的会话续接（和这个镜头的首帧共用 shot 对话）。"""
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from backend.engines.base import VideoRequest
from backend.engines.browser.conversations import (CONTINUE_VIDEO_PREFIX, Scope, conversation_id, scope_for_job,
                                                   use_scope)
from backend.engines.browser.engines import MuseVideoEngine, web_video_prompt
from backend.engines.browser.errors import BrowserError
from backend.engines.browser.sites import GEMINI, MUSE
from backend.tests.fakes.fake_browser import png
from backend.tests.test_browser_extension import EXT, hello, send_image, wait_for


# ---------------------------------------------------------------- 会话续接（上游的 conversations.py）
def test_video_shot_shares_scope_with_keyframe_and_muse_conversation_id():
    # 网页出视频和这个镜头的首帧在同一网站上共用一条对话
    assert scope_for_job("video.shot", {"shot_id": 9}, 1) == Scope("shot", "9", 1)
    assert scope_for_job("video.shot", {"shot_id": 9}, 1) == scope_for_job("image.keyframe", {"shot_id": 9}, 1)
    assert conversation_id(MUSE, "https://muse.ai/thread/48629b31-5210-43c3-ad51-6fe8295e032d") == \
        "48629b31-5210-43c3-ad51-6fe8295e032d"
    assert conversation_id(MUSE, "https://muse.ai/thread/new") == ""       # 新对话入口
    assert conversation_id(MUSE, "https://muse.ai/") == ""                 # 用户的主对话，不续接
    assert conversation_id(GEMINI, "https://gemini.google.com/app/1b603baa2feb1978") == "1b603baa2feb1978"


# ---------------------------------------------------------------- 引擎
def test_web_video_prompt_and_seconds(tmp_path):
    ff = tmp_path / "f.png"
    m = MuseVideoEngine()
    call = m.build(VideoRequest(prompt="lantern\nsways", first_frame=ff, seconds=4.0, min_seconds=3.4,
                                refs=[tmp_path / "chen.png"]))
    assert call.runner == "browser" and call.output_kind == "video" and call.model_family == "muse_web"
    assert call.workflow["kind"] == "video" and call.workflow["refs"] == [str(ff)]
    assert call.workflow["fit_aspect"] is False and call.dropped_refs == [str(tmp_path / "chen.png")]
    assert "exactly 5 seconds long" in call.workflow["prompt"] and "\n" not in call.workflow["prompt"]
    assert call.meta["asked_seconds"] == 5.0 and call.meta["seconds"] == 5.0 and call.meta["frames"] == 120
    # 镜头比最长一档还长：要最长那档
    assert m.build(VideoRequest(prompt="x", first_frame=ff, seconds=12.0)).meta["asked_seconds"] == 10.0
    assert "seconds long" not in web_video_prompt("x", "16:9", None, first_frame=False)


# ---------------------------------------------------------------- 扩展桥：视频任务、对话地址
def _mp4(path: Path) -> bytes:
    ff = shutil.which("ffmpeg")
    if ff is None:
        pytest.skip("没有 ffmpeg")
    subprocess.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=72x128:d=1:r=24",
                    "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-shortest", "-c:v", "mpeg4",
                    "-c:a", "aac", str(path)], check=True)
    return path.read_bytes()


class VideoJob:
    def __init__(self, api, tmp_path, engine="gemini_video", scope=None):
        sv = api.app.state.services
        self.runner = sv.browser
        ff = tmp_path / "first.png"
        ff.write_bytes(png(72, 128))
        self.call = sv.engines.video(engine).build(VideoRequest(prompt="灯笼在雨里摇", first_frame=ff, seconds=4))
        self.run_dir = tmp_path / "run"
        self.cancel = threading.Event()
        self.logs = []
        self.files = self.error = None
        self.scope = scope
        self.t = threading.Thread(target=self._go, daemon=True)
        self.t.start()

    def _go(self):
        try:
            with use_scope(self.scope):
                self.files = self.runner.run(self.call, run_dir=self.run_dir, cancel=self.cancel,
                                             log=self.logs.append)
        except BaseException as e:   # noqa: BLE001
            self.error = e

    def join(self):
        self.t.join(10)
        assert not self.t.is_alive()
        return self


V4 = {"version": "0.4.0", "sites": ["chatgpt", "gemini", "muse"], "features": ["rpc", "conversation", "video"]}


def test_video_job_roundtrip_saves_mp4_and_continues_shot_conversation(api, tmp_path):
    data = _mp4(tmp_path / "src.mp4")
    book = api.app.state.services.browser.conversations
    sc = Scope("shot", "9", None)
    conv = "https://gemini.google.com/app/1b603baa2feb1978"
    with api.websocket_connect("/api/browser/ext", headers=EXT) as ws:
        hello(ws, **V4)
        job = VideoJob(api, tmp_path, scope=sc)
        msg = ws.receive_json()
        assert msg["type"] == "job" and msg["kind"] == "video" and msg["site"] == "gemini"
        assert msg["aspect"] == "9:16" and msg["timeout"] == 900 and "conversation_url" not in msg
        assert [r["name"] for r in msg["refs"]] == ["first.png"]
        send_image(ws, msg["job_id"], data, chunk=4000, mime="video/mp4",
                   src="https://contribution.usercontent.google.com/download?c=1")
        ws.send_json({"type": "result", "job_id": msg["job_id"], "count": 1,
                      "conversation": {"url": conv, "continued": False, "note": ""}})
        job.join()
        assert job.error is None, job.error
        assert [f.name for f in job.files] == ["gemini_00.mp4"] and job.files[0].read_bytes() == data
        assert any("视频 72×128，1.0 秒，有声" in m for m in job.logs)
        assert wait_for(lambda: book.get("gemini", sc) is not None)

        # 同一镜头再出一段：续接那条对话，提示词开头说明是一段新视频
        job = VideoJob(api, tmp_path, scope=sc)
        msg = ws.receive_json()
        assert msg["conversation_url"] == conv and msg["prompt"].startswith(CONTINUE_VIDEO_PREFIX)
        send_image(ws, msg["job_id"], data, chunk=4000, mime="video/mp4", src="blob:https://gemini.google.com/v2")
        ws.send_json({"type": "result", "job_id": msg["job_id"], "count": 1,
                      "conversation": {"url": conv, "continued": True, "note": ""}})
        job.join()
        assert job.error is None, job.error
        assert book.get("gemini", sc)["turns"] == 2


def test_video_result_that_is_not_a_video_fails(api, tmp_path):
    with api.websocket_connect("/api/browser/ext", headers=EXT) as ws:
        hello(ws, **V4)
        job = VideoJob(api, tmp_path, engine="muse_video")
        msg = ws.receive_json()
        assert msg["site"] == "muse" and "exactly 5 seconds long" in msg["prompt"]
        send_image(ws, msg["job_id"], png(64, 64), mime="video/mp4", src="blob:https://muse.ai/x")
        ws.send_json({"type": "result", "job_id": msg["job_id"], "count": 1})
        job.join()
    assert isinstance(job.error, BrowserError) and job.error.kind == "page" and "不是能读的视频" in str(job.error)


def test_old_extension_without_video_feature_asks_reload(api, tmp_path):
    with api.websocket_connect("/api/browser/ext", headers=EXT) as ws:
        hello(ws, version="0.2.0", features=["rpc"])
        job = VideoJob(api, tmp_path).join()
    assert isinstance(job.error, BrowserError) and "重新加载" in str(job.error) and "出视频" in str(job.error)


def test_cdp_transport_rejects_web_video(api, tmp_path, monkeypatch):
    monkeypatch.setattr(api.app.state.services.browser.session.cfg, "transport", "cdp")
    job = VideoJob(api, tmp_path).join()
    assert isinstance(job.error, BrowserError) and "只支持浏览器扩展" in str(job.error)


# ---------------------------------------------------------------- Muse 也当网页文本模型
def test_web_muse_text_provider():
    from backend.config import ProviderCfg
    from backend.llm.web_providers import WEB_PROVIDERS, WebChatProvider
    from backend.tests.test_web_llm import ANSWER, Idea, gateway_for, make_runner

    assert WEB_PROVIDERS["web_muse"] == "muse"
    seen = {}

    def fake_ask(driver, spec, prompt, images, *, timeout, log, fresh, **kw):
        seen.update(spec=spec.key, text_reply=spec.text_reply, new=spec.new_chat_url)
        return ANSWER

    cfg = ProviderCfg(id="web_muse", kind="web", site="muse", timeout=300)
    prov = WebChatProvider(cfg, browser=make_runner(), chat=fake_ask, driver_factory=lambda hub, tab: None)
    out = gateway_for("web_muse", prov).generate("script", "你是编剧。", "写第一集", Idea)
    assert out.text.startswith("第一集")
    # 在 side chat 里问（不碰用户的主对话），读 assistant 消息
    assert cfg.display_name() == "Muse 网页（浏览器扩展，免费额度，较慢）"
    assert seen == {"spec": "muse", "text_reply": '[data-message-role="assistant"]',
                    "new": "https://muse.ai/thread/new"}
