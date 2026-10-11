"""API 服务（OpenAI / Anthropic / Gemini）：Key 存取和掩码、设置接口不泄露 Key、文本模型 provider、
出图 / 视频引擎的请求构造、轮询、下载、画幅映射、错误映射。全部用 httpx.MockTransport，不联网。"""
from __future__ import annotations

import base64
import io
import json
import threading
from pathlib import Path

import httpx
import pytest
from PIL import Image

from backend.config import LLMCfg, ProviderCfg
from backend.domain.api_keys import ApiKeyStore, Creds, build_creds, mask, scrub
from backend.engines.api.engines import (GeminiApiImageEngine, OpenAIImageEngine, SoraEngine,
                                         VeoEngine)
from backend.engines.api.runner import ApiError, ApiRunner
from backend.engines.base import ImageRequest, VideoRequest
from backend.llm.api_providers import (AnthropicProvider, GeminiProvider, OpenAIApiProvider,
                                       check_connection, ensure_api_providers)
from backend.llm.base import LLMError, VisionRejected
from backend.queue.errors import JobCanceled

KEY = "sk-test-SECRET-1234567890abcd"
GKEY = "AIzaSECRETgeminikey9876"
ENVS = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_BASE_URL", "ARK_API_KEY",
        "KLING_API_KEY", "KLING_ACCESS_KEY", "KLING_SECRET_KEY", "ARK_BASE_URL", "KLING_BASE_URL",
        "ANTHROPIC_BASE_URL")


@pytest.fixture(autouse=True)
def _no_env_keys(monkeypatch):
    for e in ENVS:
        monkeypatch.delenv(e, raising=False)


def png(path: Path, w: int = 64, h: int = 64, color=(200, 30, 30)) -> Path:
    Image.new("RGB", (w, h), color).save(path)
    return path


def png_bytes(w: int, h: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (10, 120, 10)).save(buf, format="PNG")
    return buf.getvalue()


def creds_fn(**keys):
    """服务 → Key 的假解析。"""
    def f(sid):
        return build_creds(sid, {"api_key": keys.get(sid)} if keys.get(sid) else None)
    return f


class Recorder:
    def __init__(self, handler):
        self.handler = handler
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        request.read()
        self.requests.append(request)
        return self.handler(request)

    def transport(self):
        return httpx.MockTransport(self)


# ---------------------------------------------------------------- Key 存取、掩码
def test_mask_and_scrub():
    assert mask(KEY) == "sk-…abcd" and KEY not in mask(KEY)
    assert mask("abcdef") == "…ef" and mask(None) is None
    assert scrub(f"Incorrect API key provided: {KEY}.", KEY) == "Incorrect API key provided: sk-…abcd."


def test_store_save_clear_env_fallback(db, monkeypatch):
    st = ApiKeyStore(db)
    c = st.resolve("openai")
    assert not c.configured and c.source is None and c.base_url == "https://api.openai.com/v1"
    assert c.model("text") == "gpt-4.1" and c.model("image") == "gpt-image-1" and c.model("video") == "sora-2"
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-key-0000001111")
    c = st.resolve("openai")
    assert c.configured and c.source == "env" and c.env_name == "OPENAI_API_KEY"
    st.update("openai", api_key=KEY, base_url="https://proxy.example.com/v1/", models={"text": "gpt-5"})
    c = st.resolve("openai")
    assert c.api_key == KEY and c.source == "settings" and c.base_url == "https://proxy.example.com/v1"
    assert c.model("text") == "gpt-5" and c.model("image") == "gpt-image-1"
    assert KEY not in repr(c)
    # 空 = 不改
    st.update("openai", api_key="", base_url=None)
    assert st.resolve("openai").api_key == KEY
    # 清除：退回环境变量；base_url 空串 = 恢复默认；模型空 = 恢复默认
    st.update("openai", clear_key=True, base_url="", models={"text": ""})
    c = st.resolve("openai")
    assert c.source == "env" and c.base_url == "https://api.openai.com/v1" and c.model("text") == "gpt-4.1"
    # 设置页存的 Key 不出现在 SettingsStore（GET /api/settings）里
    from backend.domain.settings import SettingsStore
    st.update("gemini", api_key=GKEY)
    assert "api:" not in json.dumps(SettingsStore(db).load().__dict__, default=str)
    # Gemini 认 GOOGLE_API_KEY
    st.update("gemini", clear_key=True)
    monkeypatch.setenv("GOOGLE_API_KEY", GKEY)
    assert st.resolve("gemini").env_name == "GOOGLE_API_KEY"


def test_public_has_only_mask(db):
    st = ApiKeyStore(db)
    st.update("anthropic", api_key=KEY)
    pub = st.public("anthropic")
    assert KEY not in json.dumps(pub)
    assert pub["masked"] == "sk-…abcd" and pub["configured"] and pub["source"] == "settings"
    assert pub["models"]["text"]["default"] == "claude-sonnet-4-5" and "image" not in pub["models"]


# ---------------------------------------------------------------- 设置接口
def test_api_routes_never_return_key(api):
    r = api.get("/api/api-services")
    rows = {x["id"]: x for x in r.json()["services"]}
    assert set(rows) == {"openai", "anthropic", "gemini", "ark", "kling"}
    assert rows["openai"]["configured"] is False and rows["openai"]["masked"] is None
    r = api.put("/api/api-services/openai", json={"api_key": KEY, "models": {"text": "gpt-5-mini"}})
    assert r.status_code == 200 and KEY not in r.text
    assert r.json()["masked"] == "sk-…abcd" and r.json()["models"]["text"]["effective"] == "gpt-5-mini"
    assert KEY not in api.get("/api/api-services").text
    assert KEY not in api.get("/api/settings").text and KEY not in api.get("/api/config").text
    assert KEY not in api.get("/api/engines").text
    # 字段名拼错：只报字段名，不回显值
    r = api.put("/api/api-services/openai", json={"apikey": KEY})
    assert r.status_code == 400 and KEY not in r.text and "apikey" in r.json()["detail"]
    r = api.put("/api/api-services/openai", json={"base_url": "ftp://x"})
    assert r.status_code == 400 and "http" in r.json()["detail"]
    r = api.put("/api/api-services/anthropic", json={"models": {"image": "x"}})
    assert r.status_code == 400
    assert api.put("/api/api-services/nope", json={}).status_code == 404
    # 清除
    r = api.put("/api/api-services/openai", json={"clear_key": True})
    assert r.json()["configured"] is False


def test_api_test_endpoint_records_result(api, monkeypatch):
    from backend.app.routes import api_services

    seen = {}

    def fake(sid, creds, **kw):
        seen["key"] = creds.api_key
        return {"ok": False, "message": scrub(f"OpenAI 拒绝了 API Key：{creds.api_key}", creds.api_key)}

    monkeypatch.setattr(api_services, "check_connection", fake)
    api.put("/api/api-services/openai", json={"api_key": KEY})
    r = api.post("/api/api-services/openai/test")
    assert r.status_code == 200 and r.json()["ok"] is False and KEY not in r.text
    assert seen["key"] == KEY and r.json()["service"]["last_test"]["ok"] is False
    doc = api.get("/api/doctor").text
    assert "API 服务" in doc and KEY not in doc and "测试失败" in doc


def test_settings_choices_disabled_without_key(api):
    d = api.get("/api/settings").json()
    llm = {c["id"]: c for c in d["choices"]["llm"]}
    assert llm["openai_api"]["ready"] is False and "API 服务" in llm["openai_api"]["note"]
    assert llm["manual"]["ready"] is True
    assert "gpt-4.1" in llm["openai_api"]["label"] and "claude-sonnet-4-5" in llm["anthropic_api"]["label"]
    img = {e["name"]: e for e in d["choices"]["image"]}
    assert img["openai_image"]["ready"] is False and img["openai_image"]["runtime"] == "api"
    assert "API 服务" in img["openai_image"]["note"]
    api.put("/api/api-services/openai", json={"api_key": KEY})
    d = api.get("/api/settings").json()
    llm = {c["id"]: c for c in d["choices"]["llm"]}
    assert llm["openai_api"]["ready"] is True and llm["gemini_api"]["ready"] is False
    eng = {e["name"]: e for e in api.get("/api/engines").json()["image"]}
    assert eng["openai_image"]["ready"] is True and eng["gemini_api_image"]["ready"] is False
    assert eng["openai_image"]["model"] == "gpt-image-1"
    # 能选成默认文本模型
    assert api.put("/api/settings", json={"text_model": "openai_api"}).status_code == 200


def test_api_lanes_do_not_use_gpu(api):
    m = api.app.state.manager
    assert {"openai_api_image", "gemini_api_image", "veo_api", "sora_api"} <= set(m.lanes)
    jid = m.submit("image.keyframe", {"shot_id": 1}, model_family="veo_api")
    from backend.domain.models import Job
    with api.app.state.services.db.session() as s:
        assert s.get(Job, jid).gpu is False


def test_submit_rejected_without_key(tmp_path, db):
    from backend.engines.registry import check_ready
    from backend.queue.errors import JobError
    from backend.tests.helpers import make_services

    sv = make_services(tmp_path, db)
    with pytest.raises(JobError, match="API 服务"):
        check_ready(sv.engines.video("veo_video"))


# ---------------------------------------------------------------- 文本模型
def openai_ok(request):
    return httpx.Response(200, json={"model": "gpt-4.1-2025", "choices": [{"message": {"content": '{"n": 1}'}}],
                                     "usage": {"total_tokens": 9}})


def test_openai_provider_request(tmp_path):
    rec = Recorder(openai_ok)
    p = OpenAIApiProvider(ProviderCfg(id="openai_api", kind="openai_api"), creds=creds_fn(openai=KEY),
                          transport=rec.transport())
    img = png(tmp_path / "a.png")
    res = p.complete("只输出 JSON", [{"role": "user", "content": "hi"}], images=[img])
    assert res.text == '{"n": 1}' and res.model == "gpt-4.1-2025"
    req = rec.requests[0]
    assert str(req.url) == "https://api.openai.com/v1/chat/completions"
    assert req.headers["authorization"] == f"Bearer {KEY}"
    body = json.loads(req.content)
    assert body["model"] == "gpt-4.1" and body["response_format"] == {"type": "json_object"}
    assert body["messages"][0] == {"role": "system", "content": "只输出 JSON"}
    parts = body["messages"][1]["content"]
    assert parts[0] == {"type": "text", "text": "hi"}
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")
    # 提示词里没有 json 字样：不加 response_format（OpenAI 会拒）
    p.complete("写一段话", [{"role": "user", "content": "hi"}], json_mode=True)
    assert "response_format" not in json.loads(rec.requests[1].content)


def test_openai_provider_errors_scrubbed():
    def h(request):
        return httpx.Response(401, json={"error": {"message": f"Incorrect API key provided: {KEY}",
                                                   "code": "invalid_api_key"}})

    p = OpenAIApiProvider(ProviderCfg(id="openai_api", kind="openai_api"), creds=creds_fn(openai=KEY),
                          transport=httpx.MockTransport(h))
    with pytest.raises(LLMError) as ei:
        p.complete("s", [{"role": "user", "content": "x"}])
    msg = str(ei.value)
    assert "拒绝了 API Key" in msg and KEY not in msg and "sk-…abcd" in msg


def test_provider_missing_key_hint():
    p = AnthropicProvider(ProviderCfg(id="anthropic_api", kind="anthropic"), creds=creds_fn())
    with pytest.raises(LLMError, match="API 服务.*ANTHROPIC_API_KEY"):
        p.complete("s", [{"role": "user", "content": "x"}])


def test_vision_rejected(tmp_path):
    def h(request):
        return httpx.Response(400, json={"error": {"message": "This model does not support image input"}})

    p = OpenAIApiProvider(ProviderCfg(id="openai_api", kind="openai_api"), creds=creds_fn(openai=KEY),
                          transport=httpx.MockTransport(h))
    with pytest.raises(VisionRejected):
        p.complete("s", [{"role": "user", "content": "x"}], images=[png(tmp_path / "a.png")])


def test_anthropic_provider_request(tmp_path):
    rec = Recorder(lambda r: httpx.Response(200, json={
        "model": "claude-sonnet-4-5", "content": [{"type": "text", "text": '{"n": 2}'}],
        "usage": {"input_tokens": 3}}))
    p = AnthropicProvider(ProviderCfg(id="anthropic_api", kind="anthropic"), creds=creds_fn(anthropic=KEY),
                          transport=rec.transport())
    res = p.complete("sys", [{"role": "user", "content": "看图"},
                             {"role": "assistant", "content": "x"},
                             {"role": "user", "content": "再来"}], images=[png(tmp_path / "a.png")])
    assert res.text == '{"n": 2}'
    req = rec.requests[0]
    assert str(req.url) == "https://api.anthropic.com/v1/messages"
    assert req.headers["x-api-key"] == KEY and req.headers["anthropic-version"] == "2023-06-01"
    assert "authorization" not in req.headers
    body = json.loads(req.content)
    assert body["model"] == "claude-sonnet-4-5" and body["system"] == "sys" and body["max_tokens"] > 0
    first = body["messages"][0]["content"]
    assert first[0]["type"] == "image" and first[0]["source"]["media_type"] == "image/png"
    assert first[1] == {"type": "text", "text": "看图"}
    assert body["messages"][1] == {"role": "assistant", "content": "x"}


def test_gemini_provider_request(tmp_path):
    rec = Recorder(lambda r: httpx.Response(200, json={
        "candidates": [{"content": {"parts": [{"text": '{"n": 3}'}]}, "finishReason": "STOP"}],
        "usageMetadata": {"totalTokenCount": 5}, "modelVersion": "gemini-2.5-flash"}))
    p = GeminiProvider(ProviderCfg(id="gemini_api", kind="gemini"), creds=creds_fn(gemini=GKEY),
                       transport=rec.transport())
    res = p.complete("sys", [{"role": "user", "content": "看图"}, {"role": "assistant", "content": "a"}],
                     images=[png(tmp_path / "a.png")])
    assert res.text == '{"n": 3}'
    req = rec.requests[0]
    assert str(req.url) == ("https://generativelanguage.googleapis.com/v1beta/models/"
                            "gemini-2.5-flash:generateContent")
    assert req.headers["x-goog-api-key"] == GKEY and GKEY not in str(req.url)
    body = json.loads(req.content)
    assert body["systemInstruction"] == {"parts": [{"text": "sys"}]}
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert body["contents"][0]["parts"][0]["inlineData"]["mimeType"] == "image/png"
    assert body["contents"][0]["parts"][1] == {"text": "看图"}
    assert body["contents"][1]["role"] == "model"


def test_gemini_blocked():
    p = GeminiProvider(ProviderCfg(id="gemini_api", kind="gemini"), creds=creds_fn(gemini=GKEY),
                       transport=httpx.MockTransport(lambda r: httpx.Response(
                           200, json={"promptFeedback": {"blockReason": "SAFETY"}})))
    with pytest.raises(LLMError, match="SAFETY"):
        p.complete("s", [{"role": "user", "content": "x"}])


def test_gateway_routes_to_api_provider(db):
    from pydantic import BaseModel

    from backend.domain.settings import SettingsStore
    from backend.llm.gateway import LLMGateway
    from backend.llm.providers import build_providers

    class Out(BaseModel):
        n: int

    cfg = LLMCfg(default="manual", providers=[ProviderCfg(id="manual", kind="manual")])
    ensure_api_providers(cfg)
    ensure_api_providers(cfg)        # 幂等
    assert [p.id for p in cfg.providers] == ["manual", "openai_api", "anthropic_api", "gemini_api"]
    keys = ApiKeyStore(db)
    provs = build_providers(cfg, creds=keys.resolve)
    for p in provs.values():
        p._transport = httpx.MockTransport(openai_ok)
    store = SettingsStore(db)
    store.save({"text_model": "openai_api"})
    gw = LLMGateway(cfg, provs, override=store.llm_provider)
    with pytest.raises(LLMError, match="API 服务"):
        gw.generate("script", "输出 JSON", "u", Out)
    keys.update("openai", api_key=KEY)
    assert gw.generate("script", "输出 JSON", "u", Out).n == 1


def test_check_connection():
    seen = []

    def h(request):
        seen.append(request)
        if "openai" in request.url.host:
            return httpx.Response(200, json={"data": [{"id": "gpt-4.1"}, {"id": "gpt-image-1"}]})
        if "anthropic" in request.url.host:
            return httpx.Response(403, json={"error": {"type": "permission_error", "message": "nope"}})
        return httpx.Response(200, json={"models": [{"name": "models/gemini-2.5-flash"}]})

    t = httpx.MockTransport(h)
    ok = check_connection("openai", build_creds("openai", {"api_key": KEY}), transport=t)
    assert ok["ok"] and "2 个模型" in ok["message"] and "sora-2" in ok["message"]   # sora-2 不在列表里
    assert str(seen[0].url) == "https://api.openai.com/v1/models"
    bad = check_connection("anthropic", build_creds("anthropic", {"api_key": KEY}), transport=t)
    assert not bad["ok"] and "HTTP 403" in bad["message"] and KEY not in bad["message"]
    assert seen[1].url.path == "/v1/models" and seen[1].headers["x-api-key"] == KEY
    g = check_connection("gemini", build_creds("gemini", {"api_key": GKEY}), transport=t)
    assert g["ok"] and seen[2].url.path == "/v1beta/models"
    none = check_connection("gemini", build_creds("gemini", None))
    assert not none["ok"] and "GEMINI_API_KEY" in none["message"]

    def boom(request):
        raise httpx.ConnectError("refused")

    down = check_connection("openai", build_creds("openai", {"api_key": KEY}), transport=httpx.MockTransport(boom))
    assert not down["ok"] and "连不上" in down["message"]


# ---------------------------------------------------------------- 引擎 build
def test_image_engines_build(tmp_path):
    refs = [png(tmp_path / f"r{i}.png") for i in range(10)]
    e = OpenAIImageEngine({"quality": "high"}, creds=creds_fn(openai=KEY))
    assert e.preflight() is None
    call = e.build(ImageRequest(prompt="a  girl", aspect="9:16", negative="ugly", refs=refs))
    w = call.workflow
    assert call.runner == "api" and call.model_family == "openai_api_image"
    assert w["size"] == "1024x1536" and w["model"] == "gpt-image-1" and w["quality"] == "high"
    assert len(w["refs"]) == 8 and len(call.dropped_refs) == 2
    assert "vertical 9:16" in w["prompt"] and "8 images are references" in w["prompt"]
    assert call.meta["negative_ignored"] == "ugly" and call.meta["seed"] is None
    assert KEY not in json.dumps(w) and KEY not in json.dumps(call.meta, default=str)
    assert e.build(ImageRequest(prompt="x", aspect="16:9")).workflow["size"] == "1536x1024"
    assert e.build(ImageRequest(prompt="x", aspect="1:1")).workflow["size"] == "1024x1024"
    with pytest.raises(ValueError, match="不支持画幅"):
        e.build(ImageRequest(prompt="x", aspect="5:7"))
    g = GeminiApiImageEngine(creds=creds_fn())
    assert "API 服务" in g.preflight()
    gc = g.build(ImageRequest(prompt="x", aspect="16:9", refs=refs[:5]))
    assert gc.workflow["aspect_ratio"] == "16:9" and len(gc.workflow["refs"]) == 3
    assert gc.workflow["model"] == "gemini-2.5-flash-image"


def test_video_engines_build(tmp_path):
    ff = png(tmp_path / "ff.png", 72, 128)
    v = VeoEngine(creds=creds_fn(gemini=GKEY))
    call = v.build(VideoRequest(prompt="walk", first_frame=ff, aspect="9:16", seconds=5, negative="blur",
                                refs=[ff]))
    assert call.workflow["seconds"] is None and call.workflow["negative"] == "blur"
    assert call.meta["seconds"] == 8.0 and call.meta["frames"] == 192 and call.dropped_refs == [str(ff)]
    assert "negative_ignored" not in call.meta
    v31 = VeoEngine({"model": "veo-3.1-generate-preview"}, creds=creds_fn(gemini=GKEY))
    assert v31.build(VideoRequest(prompt="x", first_frame=ff, seconds=4.5)).workflow["seconds"] == 6
    s = SoraEngine(creds=creds_fn(openai=KEY))
    c = s.build(VideoRequest(prompt="x", first_frame=ff, aspect="16:9", seconds=5, min_seconds=3, negative="n"))
    assert c.workflow["seconds"] == 8 and c.workflow["size"] == "1280x720" and c.workflow["model"] == "sora-2"
    assert c.meta["negative_ignored"] == "n"
    assert s.build(VideoRequest(prompt="x", first_frame=ff, seconds=3.5, min_seconds=4.2)).workflow["seconds"] == 8
    assert s.build(VideoRequest(prompt="x", first_frame=ff, seconds=12)).workflow["seconds"] == 12


# ---------------------------------------------------------------- runner
def b64png(w, h):
    return base64.b64encode(png_bytes(w, h)).decode()


def test_runner_openai_generations_and_crop(tmp_path):
    rec = Recorder(lambda r: httpx.Response(200, json={"data": [{"b64_json": b64png(1024, 1536)}]}))
    e = OpenAIImageEngine(creds=creds_fn(openai=KEY))
    runner = ApiRunner(creds_fn(openai=KEY), transport=rec.transport())
    logs = []
    files = runner.run(e.build(ImageRequest(prompt="p", aspect="9:16")), run_dir=tmp_path / "run",
                       cancel=threading.Event(), log=logs.append)
    req = rec.requests[0]
    assert str(req.url) == "https://api.openai.com/v1/images/generations"
    assert json.loads(req.content)["size"] == "1024x1536"
    assert Image.open(files[0]).size == (864, 1536)
    wf = (tmp_path / "run" / "workflow.api.json").read_text(encoding="utf-8")
    assert KEY not in wf and KEY not in (tmp_path / "run" / "call.json").read_text(encoding="utf-8")
    assert KEY not in " ".join(logs)


def test_runner_openai_edits_multipart(tmp_path):
    rec = Recorder(lambda r: httpx.Response(200, json={"data": [{"b64_json": b64png(1024, 1024)}]}))
    refs = [png(tmp_path / "a.png"), png(tmp_path / "b.png")]
    e = OpenAIImageEngine(creds=creds_fn(openai=KEY))
    runner = ApiRunner(creds_fn(openai=KEY), transport=rec.transport())
    files = runner.run(e.build(ImageRequest(prompt="p", aspect="1:1", refs=refs)), run_dir=tmp_path / "run",
                       cancel=threading.Event())
    req = rec.requests[0]
    assert req.url.path == "/v1/images/edits" and req.headers["content-type"].startswith("multipart/form-data")
    body = req.content
    assert body.count(b'name="image[]"') == 2 and b'name="model"' in body and b"gpt-image-1" in body
    assert Image.open(files[0]).size == (1024, 1024)


def test_runner_gemini_image(tmp_path):
    rec = Recorder(lambda r: httpx.Response(200, json={"candidates": [{"content": {"parts": [
        {"text": "here"}, {"inlineData": {"mimeType": "image/png", "data": b64png(768, 1344)}}]}}]}))
    e = GeminiApiImageEngine(creds=creds_fn(gemini=GKEY))
    runner = ApiRunner(creds_fn(gemini=GKEY), transport=rec.transport())
    files = runner.run(e.build(ImageRequest(prompt="p", aspect="9:16", refs=[png(tmp_path / "a.png")])),
                       run_dir=tmp_path / "run", cancel=threading.Event())
    body = json.loads(rec.requests[0].content)
    assert rec.requests[0].url.path == "/v1beta/models/gemini-2.5-flash-image:generateContent"
    assert body["generationConfig"] == {"responseModalities": ["IMAGE"], "imageConfig": {"aspectRatio": "9:16"}}
    assert body["contents"][0]["parts"][0]["inlineData"]["mimeType"] == "image/png"
    assert files and files[0].exists()


def test_runner_gemini_image_no_image(tmp_path):
    runner = ApiRunner(creds_fn(gemini=GKEY), transport=httpx.MockTransport(lambda r: httpx.Response(
        200, json={"candidates": [{"content": {"parts": [{"text": "I can't"}]}, "finishReason": "IMAGE_SAFETY"}]})))
    e = GeminiApiImageEngine(creds=creds_fn(gemini=GKEY))
    with pytest.raises(ApiError, match="IMAGE_SAFETY"):
        runner.run(e.build(ImageRequest(prompt="p")), run_dir=tmp_path / "r", cancel=threading.Event())


def test_runner_veo_poll_and_download(tmp_path):
    polls = {"n": 0}
    video = b"\x00\x00\x00\x18ftypmp42fake"

    def h(request):
        p = request.url.path
        if p.endswith(":predictLongRunning"):
            return httpx.Response(200, json={"name": "models/veo-3.0-generate-001/operations/op1"})
        if p == "/v1beta/models/veo-3.0-generate-001/operations/op1":
            polls["n"] += 1
            if polls["n"] < 3:
                return httpx.Response(200, json={"name": "x", "done": False})
            return httpx.Response(200, json={"done": True, "response": {"generateVideoResponse": {
                "generatedSamples": [{"video": {"uri": "https://generativelanguage.googleapis.com/v1beta/files/f1:download?alt=media"}}]}}})
        if p == "/v1beta/files/f1:download":
            assert request.headers["x-goog-api-key"] == GKEY
            return httpx.Response(200, content=video)
        return httpx.Response(404)

    rec = Recorder(h)
    ff = png(tmp_path / "ff.png", 72, 128)
    e = VeoEngine(creds=creds_fn(gemini=GKEY))
    runner = ApiRunner(creds_fn(gemini=GKEY), transport=rec.transport(), poll_interval=0)
    files = runner.run(e.build(VideoRequest(prompt="walk", first_frame=ff, aspect="9:16", negative="blur")),
                       run_dir=tmp_path / "run", cancel=threading.Event())
    assert files[0].read_bytes() == video and files[0].suffix == ".mp4"
    body = json.loads(rec.requests[0].content)
    inst = body["instances"][0]
    assert inst["prompt"] == "walk" and inst["image"]["mimeType"] == "image/png"
    assert base64.b64decode(inst["image"]["bytesBase64Encoded"]) == ff.read_bytes()
    assert body["parameters"] == {"aspectRatio": "9:16", "negativePrompt": "blur"}
    assert polls["n"] == 3


def test_runner_veo_error_and_cancel(tmp_path):
    def h(request):
        if request.url.path.endswith(":predictLongRunning"):
            return httpx.Response(200, json={"name": "models/v/operations/o"})
        return httpx.Response(200, json={"done": True, "error": {"code": 3, "message": "person generation blocked"}})

    ff = png(tmp_path / "ff.png")
    e = VeoEngine(creds=creds_fn(gemini=GKEY))
    runner = ApiRunner(creds_fn(gemini=GKEY), transport=httpx.MockTransport(h), poll_interval=0)
    with pytest.raises(ApiError, match="Veo 生成失败：person generation blocked"):
        runner.run(e.build(VideoRequest(prompt="x", first_frame=ff)), run_dir=tmp_path / "r", cancel=threading.Event())
    ev = threading.Event()
    ev.set()
    with pytest.raises(JobCanceled):
        runner.run(e.build(VideoRequest(prompt="x", first_frame=ff)), run_dir=tmp_path / "r2", cancel=ev)


def test_runner_veo_foreign_uri_has_no_key(tmp_path):
    def h(request):
        if request.url.path.endswith(":predictLongRunning"):
            return httpx.Response(200, json={"name": "models/v/operations/o"})
        if request.url.host == "cdn.example.com":
            assert "x-goog-api-key" not in request.headers
            return httpx.Response(200, content=b"vid")
        return httpx.Response(200, json={"done": True, "response": {"generateVideoResponse": {
            "generatedSamples": [{"video": {"uri": "https://cdn.example.com/v.mp4"}}]}}})

    runner = ApiRunner(creds_fn(gemini=GKEY), transport=httpx.MockTransport(h), poll_interval=0)
    e = VeoEngine(creds=creds_fn(gemini=GKEY))
    files = runner.run(e.build(VideoRequest(prompt="x", first_frame=png(tmp_path / "f.png"))),
                       run_dir=tmp_path / "r", cancel=threading.Event())
    assert files[0].read_bytes() == b"vid"


def test_runner_sora(tmp_path):
    states = iter(["queued", "in_progress", "completed"])

    def h(request):
        p = request.url.path
        if request.method == "POST" and p == "/v1/videos":
            return httpx.Response(200, json={"id": "video_1", "status": "queued"})
        if p == "/v1/videos/video_1":
            return httpx.Response(200, json={"id": "video_1", "status": next(states), "progress": 50})
        if p == "/v1/videos/video_1/content":
            return httpx.Response(200, content=b"mp4data")
        return httpx.Response(404)

    rec = Recorder(h)
    ff = png(tmp_path / "ff.png", 90, 160)
    e = SoraEngine(creds=creds_fn(openai=KEY))
    runner = ApiRunner(creds_fn(openai=KEY), transport=rec.transport(), poll_interval=0)
    prog, logs = [], []
    files = runner.run(e.build(VideoRequest(prompt="go", first_frame=ff, aspect="9:16", seconds=4)),
                       run_dir=tmp_path / "run", cancel=threading.Event(),
                       on_progress=lambda a, b: prog.append(a), log=logs.append)
    assert files[0].read_bytes() == b"mp4data"
    sub = rec.requests[0]
    assert sub.headers["authorization"] == f"Bearer {KEY}"
    body = sub.content
    assert b'name="input_reference"' in body and b'name="seconds"' in body and b"720x1280" in body
    # 首帧缩到 720x1280 再传
    start = body.index(b"\x89PNG")
    assert Image.open(io.BytesIO(body[start:])).size == (720, 1280)
    assert prog and any("完成" in m for m in logs)


def test_runner_sora_failed_and_http_errors(tmp_path):
    def h(request):
        if request.method == "POST":
            return httpx.Response(200, json={"id": "v2", "status": "queued"})
        return httpx.Response(200, json={"id": "v2", "status": "failed", "error": {"message": "moderation"}})

    e = SoraEngine(creds=creds_fn(openai=KEY))
    ff = png(tmp_path / "f.png")
    runner = ApiRunner(creds_fn(openai=KEY), transport=httpx.MockTransport(h), poll_interval=0)
    with pytest.raises(ApiError, match="Sora 生成失败（failed）：moderation"):
        runner.run(e.build(VideoRequest(prompt="x", first_frame=ff)), run_dir=tmp_path / "r", cancel=threading.Event())
    r429 = ApiRunner(creds_fn(openai=KEY), transport=httpx.MockTransport(
        lambda r: httpx.Response(429, json={"error": {"message": "quota"}})))
    with pytest.raises(ApiError, match="限流或额度用完"):
        r429.run(e.build(VideoRequest(prompt="x", first_frame=ff)), run_dir=tmp_path / "r2", cancel=threading.Event())
    r404 = ApiRunner(creds_fn(openai=KEY), transport=httpx.MockTransport(
        lambda r: httpx.Response(404, json={"error": {"message": "model not found"}})))
    with pytest.raises(ApiError, match="模型名 sora-2 可能写错"):
        r404.run(e.build(VideoRequest(prompt="x", first_frame=ff)), run_dir=tmp_path / "r3", cancel=threading.Event())
    nokey = ApiRunner(creds_fn())
    with pytest.raises(ApiError, match="API 服务"):
        nokey.run(e.build(VideoRequest(prompt="x", first_frame=ff)), run_dir=tmp_path / "r4", cancel=threading.Event())


def test_run_comfy_dispatches_api(tmp_path, db):
    from backend.pipeline.keyframes import run_comfy
    from backend.queue.errors import JobError
    from backend.tests.helpers import FakeCtx, make_services

    sv = make_services(tmp_path, db)
    sv.api = ApiRunner(creds_fn(), transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    e = OpenAIImageEngine(creds=creds_fn(openai=KEY))
    with pytest.raises(JobError, match="API 服务"):
        run_comfy(FakeCtx(sv), e.build(ImageRequest(prompt="p")), "demo")
    sv.api = ApiRunner(creds_fn(openai=KEY), transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"data": [{"b64_json": b64png(1024, 1536)}]})))
    files, run_dir = run_comfy(FakeCtx(sv), e.build(ImageRequest(prompt="p")), "demo")
    assert files[0].exists() and (run_dir / "workflow.api.json").exists()


def test_doctor_api_services(db, monkeypatch):
    from backend.doctor import check_api_services
    from backend.domain.settings import SettingsStore

    keys, st = ApiKeyStore(db), SettingsStore(db)
    rows = {c.name: c for c in check_api_services(keys, st)}
    assert all(c.status == "ok" and "未配置" in c.detail for c in rows.values())
    st.save({"video_engine": "veo_video"})
    rows = {c.name: c for c in check_api_services(keys, st)}
    assert rows["Google（Gemini）"].status == "warn" and "veo_video" in rows["Google（Gemini）"].detail
    keys.update("openai", api_key=KEY)
    keys.last_test["openai"] = {"ok": True, "message": "连接成功"}
    rows = {c.name: c for c in check_api_services(keys, st)}
    assert rows["OpenAI（GPT）"].status == "ok" and "sk-…abcd" in rows["OpenAI（GPT）"].detail
    assert KEY not in rows["OpenAI（GPT）"].detail


def test_model_precedence():
    c = build_creds("openai", {"api_key": KEY})
    assert c.model("text") == "gpt-4.1" and c.model("text", "gpt-4o") == "gpt-4o"   # config 里写的
    c = build_creds("openai", {"api_key": KEY, "models": {"text": "gpt-5"}})
    assert c.model("text", "gpt-4o") == "gpt-5"                                        # 设置页填的优先
    e = VeoEngine({"model": "veo-3.1-generate-preview"},
                  creds=lambda sid: build_creds(sid, {"api_key": GKEY, "models": {"video": "veo-3.0-fast-generate-001"}}))
    assert e.model() == "veo-3.0-fast-generate-001"


def test_creds_require():
    c = Creds("openai")
    with pytest.raises(Exception, match="OPENAI_API_KEY"):
        c.require()
