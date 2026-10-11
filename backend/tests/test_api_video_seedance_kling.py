"""Seedance（火山方舟）和可灵 API 视频引擎：区域和 Base URL、可灵 API Key / AK+SK 两种认证和 JWT 签名、
引擎 build（首帧、尾帧、时长档位）、runner 的请求结构、轮询、下载、失败原因、超时、取消，
以及 Key / SecretKey / JWT 不进 workflow.api.json、call.json、日志和错误信息。全部假 transport，不联网。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import threading
from pathlib import Path

import httpx
import pytest
from PIL import Image

from backend.domain.api_keys import ApiKeyStore, build_creds, scrub
from backend.engines.api.engines import KlingEngine, SeedanceEngine, SoraEngine, VeoEngine
from backend.engines.api.runner import ApiError, ApiRunner
from backend.engines.base import VideoRequest
from backend.llm.api_http import KlingJwtAuth, kling_jwt
from backend.llm.api_providers import check_connection
from backend.queue.errors import JobCanceled

ARK_KEY = "ark-SECRET-key-0123456789abcdef"
KLING_KEY = "kling-SECRET-apikey-0123456789"
AK = "AKkling-access-0123456789"
SK = "SKkling-secret-9876543210zyxw"
ENVS = ("ARK_API_KEY", "ARK_BASE_URL", "KLING_API_KEY", "KLING_ACCESS_KEY", "KLING_SECRET_KEY", "KLING_BASE_URL")
VIDEO = b"\x00\x00\x00\x18ftypmp42seedance"


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    for e in ENVS:
        monkeypatch.delenv(e, raising=False)


def png(path: Path, w: int = 72, h: int = 128, color=(200, 30, 30)) -> Path:
    Image.new("RGB", (w, h), color).save(path)
    return path


def creds_fn(rows: dict[str, dict]):
    return lambda sid: build_creds(sid, rows.get(sid))


def b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def verify_jwt(token: str, secret: str) -> tuple[dict, dict]:
    h, p, sig = token.split(".")
    want = hmac.new(secret.encode(), f"{h}.{p}".encode(), hashlib.sha256).digest()
    assert hmac.compare_digest(b64url_decode(sig), want), "签名不对"
    return json.loads(b64url_decode(h)), json.loads(b64url_decode(p))


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


def files_text(run_dir: Path) -> str:
    return ((run_dir / "workflow.api.json").read_text(encoding="utf-8")
            + (run_dir / "call.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- JWT
def test_kling_jwt_claims_and_signature():
    tok = kling_jwt(AK, SK, now=1_760_000_000)
    header, payload = verify_jwt(tok, SK)
    assert header == {"alg": "HS256", "typ": "JWT"}
    assert payload == {"iss": AK, "exp": 1_760_000_000 + 1800, "nbf": 1_760_000_000 - 5}
    # 换个 SK 验不过；同一时刻签出来一样（确定性）
    with pytest.raises(AssertionError):
        verify_jwt(tok, SK + "x")
    assert kling_jwt(AK, SK, now=1_760_000_000) == tok
    # scrub 会把像 JWT 的串整个隐藏
    assert tok not in scrub(f"bad token {tok}") and "令牌已隐藏" in scrub(f"bad token {tok}")


def test_kling_jwt_auth_signs_each_request():
    clock = iter([1000.0, 2000.0])
    seen = []
    t = httpx.MockTransport(lambda r: (seen.append(r.headers["authorization"]), httpx.Response(200, json={}))[1])
    with httpx.Client(transport=t, auth=KlingJwtAuth(AK, SK, clock=lambda: next(clock))) as c:
        c.get("https://x.test/a")
        c.get("https://x.test/b")
    p1 = verify_jwt(seen[0].removeprefix("Bearer "), SK)[1]
    p2 = verify_jwt(seen[1].removeprefix("Bearer "), SK)[1]
    assert p1["nbf"] == 995 and p2["exp"] == 3800 and seen[0] != seen[1]


# ---------------------------------------------------------------- 区域、Key
def test_region_and_base_url():
    c = build_creds("ark", None)
    assert c.region == "cn" and c.base_url == "https://ark.cn-beijing.volces.com/api/v3" and not c.configured
    c = build_creds("ark", {"api_key": ARK_KEY, "region": "intl"})
    assert c.base_url == "https://ark.ap-southeast.bytepluses.com/api/v3" and c.configured
    # 自己填的 Base URL 优先于区域
    c = build_creds("ark", {"api_key": ARK_KEY, "region": "intl", "base_url": "https://proxy.example.com/api/v3/"})
    assert c.base_url == "https://proxy.example.com/api/v3"
    assert build_creds("kling", None).base_url == "https://api-beijing.klingai.com"
    assert build_creds("kling", {"region": "intl"}).base_url == "https://api-singapore.klingai.com"
    # 不认得的区域按默认
    assert build_creds("kling", {"region": "mars"}).region == "cn"
    # 没区域的服务
    assert build_creds("openai", None).region is None


def test_kling_auth_sources(monkeypatch):
    c = build_creds("kling", {"access_key": AK, "secret_key": SK})
    assert c.configured and c.aksk and c.source == "settings" and c.require() == AK
    assert AK not in repr(c) and SK not in repr(c) and set(c.secrets()) == {AK, SK}
    # 只有一半不算配好
    assert not build_creds("kling", {"access_key": AK}).configured
    # API Key 优先
    c = build_creds("kling", {"api_key": KLING_KEY, "access_key": AK, "secret_key": SK})
    assert c.configured and not c.aksk and c.api_key == KLING_KEY
    # 环境变量的 AK/SK
    monkeypatch.setenv("KLING_ACCESS_KEY", AK)
    monkeypatch.setenv("KLING_SECRET_KEY", SK)
    c = build_creds("kling", None)
    assert c.aksk and c.source == "env" and c.env_name == "KLING_ACCESS_KEY + KLING_SECRET_KEY"
    monkeypatch.setenv("KLING_API_KEY", KLING_KEY)
    assert build_creds("kling", None).env_name == "KLING_API_KEY"
    # 火山方舟不收 AK/SK
    assert not build_creds("ark", {"access_key": AK, "secret_key": SK}).configured


def test_store_region_and_aksk(db):
    st = ApiKeyStore(db)
    st.update("kling", access_key=AK, secret_key=SK, region="intl")
    c = st.resolve("kling")
    assert c.aksk and c.base_url == "https://api-singapore.klingai.com"
    pub = st.public("kling")
    text = json.dumps(pub)
    assert AK not in text and SK not in text
    assert pub["auth"] == "aksk" and pub["aksk"] and pub["has_saved_aksk"] and pub["has_saved_key"]
    assert pub["region"] == "intl" and pub["default_base_url"] == "https://api-singapore.klingai.com"
    assert [r["id"] for r in pub["regions"]] == ["cn", "intl"]
    assert pub["masked"] and pub["masked"] != AK
    # 区域空串 = 恢复国内；清除把 AK/SK 一起删
    st.update("kling", region="")
    assert st.resolve("kling").region == "cn"
    st.update("kling", clear_key=True)
    assert not st.resolve("kling").configured
    assert st.public("openai")["regions"] == [] and st.public("openai")["region"] is None
    assert st.public("openai")["aksk"] is False


def test_routes_region_and_aksk(api):
    r = api.put("/api/api-services/kling", json={"access_key": AK})
    assert r.status_code == 400 and "一起填" in r.json()["detail"] and AK not in r.text
    r = api.put("/api/api-services/kling", json={"access_key": AK, "secret_key": SK, "region": "intl"})
    assert r.status_code == 200 and AK not in r.text and SK not in r.text
    assert r.json()["configured"] and r.json()["auth"] == "aksk" and r.json()["region"] == "intl"
    # 已经存过 AK，只换 SK 可以
    r = api.put("/api/api-services/kling", json={"secret_key": SK + "2"})
    assert r.status_code == 200
    r = api.put("/api/api-services/ark", json={"access_key": AK, "secret_key": SK})
    assert r.status_code == 400 and AK not in r.text
    assert api.put("/api/api-services/ark", json={"region": "moon"}).status_code == 400
    assert api.put("/api/api-services/openai", json={"region": "intl"}).status_code == 400
    r = api.put("/api/api-services/ark", json={"api_key": ARK_KEY, "region": "intl"})
    assert r.status_code == 200 and ARK_KEY not in r.text
    assert r.json()["effective_base_url"] == "https://ark.ap-southeast.bytepluses.com/api/v3"
    for path in ("/api/api-services", "/api/engines", "/api/settings", "/api/doctor"):
        t = api.get(path).text
        assert ARK_KEY not in t and AK not in t and SK not in t
    eng = {e["name"]: e for e in api.get("/api/engines").json()["video"]}
    assert eng["seedance_video"]["ready"] is True and eng["kling_video"]["ready"] is True
    assert eng["seedance_video"]["caps"]["supports_end_frame"] is True
    assert {"seedance_api", "kling_api"} <= set(api.app.state.manager.lanes)


# ---------------------------------------------------------------- build
def test_build_end_frame_and_refs(tmp_path):
    ff, ef = png(tmp_path / "ff.png"), png(tmp_path / "ef.png")
    ref = png(tmp_path / "ref.png")
    s = SeedanceEngine(creds=creds_fn({"ark": {"api_key": ARK_KEY}}))
    call = s.build(VideoRequest(prompt="walk", first_frame=ff, aspect="9:16", seconds=5.2, negative="blur"))
    w = call.workflow
    assert w["service"] == "ark" and w["model"] == "doubao-seedance-2-0-260128" and w["seconds"] == 6
    assert "end_frame" not in w and w["generate_audio"] is True and w["resolution"] == "720p"
    assert call.meta["negative_ignored"] == "blur" and call.model_family == "seedance_api"
    call = s.build(VideoRequest(prompt="walk", first_frame=ff, end_frame=ef, refs=[ref], seconds=20))
    assert call.workflow["end_frame"] == str(ef) and call.meta["end_frame"] == str(ef)
    # 参考图和首帧互斥（refs_exclusive）：有首帧时参考图不带
    assert call.workflow["seconds"] == 15 and call.dropped_refs == [str(ref)] and "refs" not in call.workflow
    assert s.build(VideoRequest(prompt="x", first_frame=ff, seconds=2)).workflow["seconds"] == 4
    old = SeedanceEngine({"model": "doubao-seedance-1-0-pro-250528"}, creds=creds_fn({"ark": {"api_key": ARK_KEY}}))
    assert old.build(VideoRequest(prompt="x", first_frame=ff, seconds=2)).workflow["generate_audio"] is None
    assert old.build(VideoRequest(prompt="x", first_frame=ff, seconds=2)).workflow["seconds"] == 2
    assert ARK_KEY not in json.dumps(call.workflow)
    # Veo / Sora 不支持尾帧：照旧不带，记成没带上
    v = VeoEngine(creds=creds_fn({}))
    vc = v.build(VideoRequest(prompt="x", first_frame=ff, end_frame=ef, refs=[ref]))
    assert "end_frame" not in vc.workflow and vc.dropped_refs == [str(ref), str(ef)] and vc.meta["refs"] == []
    sc = SoraEngine(creds=creds_fn({})).build(VideoRequest(prompt="x", first_frame=ff))
    assert "end_frame" not in sc.workflow and "refs" not in sc.workflow and sc.dropped_refs == []


def test_build_kling_modes_and_seconds(tmp_path):
    ff, ef = png(tmp_path / "ff.png"), png(tmp_path / "ef.png")
    k = KlingEngine(creds=creds_fn({"kling": {"api_key": KLING_KEY}}))
    c = k.build(VideoRequest(prompt="go", first_frame=ff, seconds=6.5, negative="ugly"))
    assert c.workflow["model"] == "kling-v3" and c.workflow["seconds"] == 7 and c.workflow["mode"] == "std"
    assert c.workflow["sound"] == "on" and c.workflow["negative"] == "ugly" and "negative_ignored" not in c.meta
    assert c.meta["size"] == [720, 1280] and c.meta["mode"] == "std"
    c = k.build(VideoRequest(prompt="go", first_frame=ff, end_frame=ef, aspect="16:9"))
    assert c.workflow["mode"] == "pro" and "尾帧" in c.meta["mode_forced"] and c.meta["size"] == [1920, 1080]
    assert c.workflow["end_frame"] == str(ef)
    old = KlingEngine({"model": "kling-v2-1", "cfg_scale": 0.4}, creds=creds_fn({"kling": {"api_key": KLING_KEY}}))
    oc = old.build(VideoRequest(prompt="go", first_frame=ff, seconds=6))
    assert oc.workflow["seconds"] == 10 and oc.workflow["sound"] is None and oc.workflow["cfg_scale"] == 0.4
    assert old.build(VideoRequest(prompt="go", first_frame=ff, seconds=3)).workflow["seconds"] == 5
    assert "API 服务" in KlingEngine(creds=creds_fn({})).preflight()
    assert KlingEngine(creds=creds_fn({"kling": {"access_key": AK, "secret_key": SK}})).preflight() is None


# ---------------------------------------------------------------- Seedance runner
def seedance_handler(polls: dict, *, final: dict | None = None):
    def h(request):
        p = request.url.path
        if request.method == "POST" and p.endswith("/contents/generations/tasks"):
            return httpx.Response(200, json={"id": "cgt-123"})
        if p.endswith("/contents/generations/tasks/cgt-123"):
            polls["n"] += 1
            if polls["n"] < 3:
                return httpx.Response(200, json={"id": "cgt-123", "status": "running" if polls["n"] > 1 else "queued"})
            return httpx.Response(200, json=final or {"id": "cgt-123", "status": "succeeded",
                                                      "content": {"video_url": "https://tos.example.com/v.mp4?sig=1"}})
        if request.url.host == "tos.example.com":
            assert "authorization" not in request.headers   # CDN 地址不带 Key
            return httpx.Response(200, content=VIDEO)
        return httpx.Response(404)
    return h


@pytest.mark.parametrize("with_end", [False, True])
def test_seedance_runner_request_poll_download(tmp_path, with_end):
    ff, ef = png(tmp_path / "ff.png"), png(tmp_path / "ef.png", color=(0, 0, 200))
    polls = {"n": 0}
    rec = Recorder(seedance_handler(polls))
    creds = creds_fn({"ark": {"api_key": ARK_KEY}})
    e = SeedanceEngine(creds=creds)
    runner = ApiRunner(creds, transport=rec.transport(), poll_interval=0)
    logs, prog = [], []
    call = e.build(VideoRequest(prompt="a  girl walks", first_frame=ff, aspect="9:16", seconds=5,
                                end_frame=ef if with_end else None))
    files = runner.run(call, run_dir=tmp_path / "run", cancel=threading.Event(), log=logs.append,
                       on_progress=lambda a, b: prog.append(a))
    assert files[0].read_bytes() == VIDEO and files[0].name == "seedance_00.mp4"
    sub = rec.requests[0]
    assert str(sub.url) == "https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks"
    assert sub.headers["authorization"] == f"Bearer {ARK_KEY}"
    body = json.loads(sub.content)
    assert body["model"] == "doubao-seedance-2-0-260128" and body["ratio"] == "9:16" and body["duration"] == 5
    assert body["resolution"] == "720p" and body["generate_audio"] is True and body["watermark"] is False
    assert body["content"][0] == {"type": "text", "text": "a girl walks"}
    imgs = body["content"][1:]
    assert [i["role"] for i in imgs] == (["first_frame", "last_frame"] if with_end else ["first_frame"])
    url = imgs[0]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == ff.read_bytes()
    if with_end:
        assert base64.b64decode(imgs[1]["image_url"]["url"].split(",", 1)[1]) == ef.read_bytes()
    assert polls["n"] == 3 and prog == [100]
    assert any("排队中" in m for m in logs) and any("生成中" in m for m in logs)
    assert ARK_KEY not in files_text(tmp_path / "run") and ARK_KEY not in " ".join(logs)


def test_seedance_ref_mode_build(tmp_path):
    """参考图直出：caps.ref_mode、最多 9 张；没有首帧时只带参考图，尾帧不带（要跟首帧一起）。"""
    refs = [png(tmp_path / f"r{i}.png") for i in range(11)]
    ef = png(tmp_path / "ef.png")
    s = SeedanceEngine(creds=creds_fn({"ark": {"api_key": ARK_KEY}}))
    assert s.caps.ref_mode is True and s.caps.max_refs == 9
    call = s.build(VideoRequest(prompt="two people talk", first_frame=None, refs=refs, end_frame=ef, seconds=5))
    w = call.workflow
    assert w["first_frame"] is None and w["refs"] == [str(p) for p in refs[:9]] and "end_frame" not in w
    assert call.dropped_refs == [str(refs[9]), str(refs[10]), str(ef)]
    assert call.meta["first_frame"] is None and call.meta["refs"] == w["refs"]
    # 可灵没开参考图直出：没有首帧时报清楚
    with pytest.raises(ValueError, match="不支持参考图直出"):
        KlingEngine(creds=creds_fn({"kling": {"api_key": KLING_KEY}})).build(
            VideoRequest(prompt="x", first_frame=None, refs=refs[:1]))


def test_seedance_runner_reference_images(tmp_path):
    refs = [png(tmp_path / "a.png", color=(1, 2, 3)), png(tmp_path / "b.png", color=(9, 9, 9))]
    rec = Recorder(seedance_handler({"n": 0}))
    creds = creds_fn({"ark": {"api_key": ARK_KEY}})
    runner = ApiRunner(creds, transport=rec.transport(), poll_interval=0)
    logs: list[str] = []
    call = SeedanceEngine(creds=creds).build(VideoRequest(prompt="go", first_frame=None, refs=refs, seconds=5))
    files = runner.run(call, run_dir=tmp_path / "run", cancel=threading.Event(), log=logs.append)
    assert files[0].read_bytes() == VIDEO
    body = json.loads(rec.requests[0].content)
    imgs = body["content"][1:]
    assert [i["role"] for i in imgs] == ["reference_image", "reference_image"]
    assert [base64.b64decode(i["image_url"]["url"].split(",", 1)[1]) for i in imgs] == [p.read_bytes() for p in refs]
    assert all(i["type"] == "image_url" for i in imgs)
    assert any("参考图直出 2 张" in m for m in logs)
    assert ARK_KEY not in files_text(tmp_path / "run")


def test_seedance_runner_first_frame_ignores_refs(tmp_path):
    """首帧模式下就算 workflow 里有 refs（手写的老任务），也只发首帧：三种输入方式互斥。"""
    ff, r = png(tmp_path / "ff.png"), png(tmp_path / "r.png")
    rec = Recorder(seedance_handler({"n": 0}))
    creds = creds_fn({"ark": {"api_key": ARK_KEY}})
    call = SeedanceEngine(creds=creds).build(VideoRequest(prompt="go", first_frame=ff, seconds=5))
    call.workflow["refs"] = [str(r)]
    ApiRunner(creds, transport=rec.transport(), poll_interval=0).run(
        call, run_dir=tmp_path / "run", cancel=threading.Event())
    body = json.loads(rec.requests[0].content)
    assert [i["role"] for i in body["content"][1:]] == ["first_frame"]


def test_seedance_region_intl(tmp_path):
    rec = Recorder(seedance_handler({"n": 5}))
    creds = creds_fn({"ark": {"api_key": ARK_KEY, "region": "intl"}})
    runner = ApiRunner(creds, transport=rec.transport(), poll_interval=0)
    runner.run(SeedanceEngine(creds=creds).build(VideoRequest(prompt="x", first_frame=png(tmp_path / "f.png"))),
               run_dir=tmp_path / "r", cancel=threading.Event())
    assert rec.requests[0].url.host == "ark.ap-southeast.bytepluses.com"
    assert rec.requests[1].url.path == "/api/v3/contents/generations/tasks/cgt-123"


def test_seedance_failures(tmp_path):
    ff = png(tmp_path / "f.png")
    creds = creds_fn({"ark": {"api_key": ARK_KEY}})
    e = SeedanceEngine(creds=creds)

    def run(handler, sub="r"):
        runner = ApiRunner(creds, transport=httpx.MockTransport(handler), poll_interval=0)
        return runner.run(e.build(VideoRequest(prompt="x", first_frame=ff)), run_dir=tmp_path / sub,
                          cancel=threading.Event())

    # 任务失败：内容审核
    h = seedance_handler({"n": 5}, final={"id": "cgt-123", "status": "failed", "error": {
        "code": "OutputVideoSensitiveContentDetected", "message": "The generated video may contain sensitive content"}})
    with pytest.raises(ApiError, match="Seedance 生成失败（失败）：生成的视频没通过内容审核") as ei:
        run(h)
    assert "OutputVideoSensitiveContentDetected" in str(ei.value)
    # 欠费（HTTP 403）
    with pytest.raises(ApiError, match="欠费或余额不足"):
        run(lambda r: httpx.Response(403, json={"error": {"code": "AccountOverdueError",
                                                          "message": f"overdue for key {ARK_KEY}"}}), "r2")
    # 401：Key 不对，回显的 Key 被掩掉
    with pytest.raises(ApiError) as ei:
        run(lambda r: httpx.Response(401, json={"error": {"code": "AuthenticationError",
                                                          "message": f"bad key {ARK_KEY}"}}), "r3")
    assert "401" in str(ei.value) and "API Key 不对" in str(ei.value) and ARK_KEY not in str(ei.value)
    # 没开通模型
    with pytest.raises(ApiError, match="开通"):
        run(lambda r: httpx.Response(404, json={"error": {"code": "ModelNotOpen", "message": "not activated"}}), "r4")
    # 成功但没给地址
    with pytest.raises(ApiError, match="没有返回视频地址"):
        run(seedance_handler({"n": 5}, final={"id": "cgt-123", "status": "succeeded", "content": {}}), "r5")
    # 没 Key：不发请求
    with pytest.raises(ApiError, match="ARK_API_KEY"):
        ApiRunner(creds_fn({})).run(e.build(VideoRequest(prompt="x", first_frame=ff)), run_dir=tmp_path / "r6",
                                    cancel=threading.Event())


def test_seedance_timeout_and_cancel(tmp_path):
    ff = png(tmp_path / "f.png")
    creds = creds_fn({"ark": {"api_key": ARK_KEY}})

    def never(request):
        if request.method == "POST":
            return httpx.Response(200, json={"id": "cgt-123"})
        return httpx.Response(200, json={"id": "cgt-123", "status": "running"})

    e = SeedanceEngine({"timeout": 0.01}, creds=creds)
    runner = ApiRunner(creds, transport=httpx.MockTransport(never), poll_interval=0.02)
    with pytest.raises(ApiError, match="超过 0 秒没完成"):
        runner.run(e.build(VideoRequest(prompt="x", first_frame=ff)), run_dir=tmp_path / "r", cancel=threading.Event())
    ev = threading.Event()

    def cancel_on_poll(request):
        if request.method == "GET":
            ev.set()
        return never(request)

    runner = ApiRunner(creds, transport=httpx.MockTransport(cancel_on_poll), poll_interval=0)
    with pytest.raises(JobCanceled):
        runner.run(SeedanceEngine(creds=creds).build(VideoRequest(prompt="x", first_frame=ff)),
                   run_dir=tmp_path / "r2", cancel=ev)


# ---------------------------------------------------------------- 可灵 runner
def kling_handler(polls: dict, *, final: dict | None = None, cdn: str = "https://cdn.klingai.test/v.mp4"):
    def h(request):
        p = request.url.path
        if request.method == "POST" and p == "/v1/videos/image2video":
            return httpx.Response(200, json={"code": 0, "message": "SUCCEED", "request_id": "r1",
                                             "data": {"task_id": "k-1", "task_status": "submitted"}})
        if p == "/v1/videos/image2video/k-1":
            polls["n"] += 1
            if polls["n"] < 2:
                return httpx.Response(200, json={"code": 0, "data": {"task_id": "k-1", "task_status": "processing"}})
            return httpx.Response(200, json={"code": 0, "data": final or {
                "task_id": "k-1", "task_status": "succeed",
                "task_result": {"videos": [{"id": "v1", "url": cdn, "duration": "5"}]}}})
        if request.url.host == "cdn.klingai.test":
            assert "authorization" not in request.headers
            return httpx.Response(200, content=VIDEO)
        return httpx.Response(404)
    return h


@pytest.mark.parametrize("with_end", [False, True])
def test_kling_runner_api_key(tmp_path, with_end):
    ff, ef = png(tmp_path / "ff.png"), png(tmp_path / "ef.png", color=(0, 0, 200))
    polls = {"n": 0}
    rec = Recorder(kling_handler(polls))
    creds = creds_fn({"kling": {"api_key": KLING_KEY}})
    runner = ApiRunner(creds, transport=rec.transport(), poll_interval=0)
    logs = []
    call = KlingEngine(creds=creds).build(VideoRequest(prompt="p" * 3000, first_frame=ff, seconds=5,
                                                       negative="blur", end_frame=ef if with_end else None))
    files = runner.run(call, run_dir=tmp_path / "run", cancel=threading.Event(), log=logs.append)
    assert files[0].read_bytes() == VIDEO and files[0].name == "kling_00.mp4"
    sub = rec.requests[0]
    assert str(sub.url) == "https://api-beijing.klingai.com/v1/videos/image2video"
    assert sub.headers["authorization"] == f"Bearer {KLING_KEY}"
    body = json.loads(sub.content)
    assert body["model_name"] == "kling-v3" and body["duration"] == "5" and body["sound"] == "on"
    assert body["negative_prompt"] == "blur" and len(body["prompt"]) == 2500
    assert not body["image"].startswith("data:") and base64.b64decode(body["image"]) == ff.read_bytes()
    assert "aspect_ratio" not in body
    if with_end:
        assert body["mode"] == "pro" and base64.b64decode(body["image_tail"]) == ef.read_bytes()
        assert any("pro" in m for m in logs)
    else:
        assert body["mode"] == "std" and "image_tail" not in body
    assert polls["n"] == 2 and any("生成中" in m for m in logs)
    assert KLING_KEY not in files_text(tmp_path / "run") and KLING_KEY not in " ".join(logs)


def test_kling_runner_aksk_jwt_never_written(tmp_path):
    rec = Recorder(kling_handler({"n": 0}))
    creds = creds_fn({"kling": {"access_key": AK, "secret_key": SK, "region": "intl"}})
    runner = ApiRunner(creds, transport=rec.transport(), poll_interval=0)
    logs = []
    call = KlingEngine(creds=creds).build(VideoRequest(prompt="go", first_frame=png(tmp_path / "f.png")))
    runner.run(call, run_dir=tmp_path / "run", cancel=threading.Event(), log=logs.append)
    api_reqs = [r for r in rec.requests if r.url.host == "api-singapore.klingai.com"]
    assert len(api_reqs) == 3
    tokens = set()
    for r in api_reqs:
        tok = r.headers["authorization"].removeprefix("Bearer ")
        header, payload = verify_jwt(tok, SK)
        assert header["alg"] == "HS256" and payload["iss"] == AK and payload["exp"] - payload["nbf"] == 1805
        tokens.add(tok)
    written = files_text(tmp_path / "run") + " ".join(logs)
    for secret in (AK, SK, *tokens):
        assert secret not in written


def test_kling_failures(tmp_path):
    ff = png(tmp_path / "f.png")
    creds = creds_fn({"kling": {"access_key": AK, "secret_key": SK}})
    e = KlingEngine(creds=creds)

    def run(handler, sub):
        runner = ApiRunner(creds, transport=httpx.MockTransport(handler), poll_interval=0)
        return runner.run(e.build(VideoRequest(prompt="x", first_frame=ff)), run_dir=tmp_path / sub,
                          cancel=threading.Event())

    # 任务失败：内容审核
    with pytest.raises(ApiError, match="可灵生成失败：内容没通过安全审核") as ei:
        run(kling_handler({"n": 5}, final={"task_id": "k-1", "task_status": "failed",
                                           "task_status_msg": "Failure to pass the risk control system"}), "r1")
    assert "risk control" in str(ei.value)
    # 余额不足：HTTP 429 + code 1102
    with pytest.raises(ApiError, match="资源包用完"):
        run(lambda r: httpx.Response(429, json={"code": 1102, "message": "Account balance not enough"}), "r2")
    # HTTP 200 但 code ≠ 0 也是错
    with pytest.raises(ApiError, match="内容没通过安全审核"):
        run(lambda r: httpx.Response(200, json={"code": 1301, "message": "content risk"}), "r3")
    # 401：签名过期，接口回显了令牌和 AK —— 都不能出现在错误里
    seen = {}

    def unauthorized(request):
        tok = request.headers["authorization"].removeprefix("Bearer ")
        seen["tok"] = tok
        return httpx.Response(401, json={"code": 1004, "message": f"token {tok} expired for {AK}"})

    with pytest.raises(ApiError) as ei:
        run(unauthorized, "r4")
    msg = str(ei.value)
    assert "401" in msg and "令牌已过期" in msg and seen["tok"] not in msg and AK not in msg and SK not in msg
    # 不认得的 401
    with pytest.raises(ApiError, match="拒绝了 API Key / 签名"):
        run(lambda r: httpx.Response(401, json={"message": "unauthorized"}), "r5")


def test_kling_timeout(tmp_path):
    creds = creds_fn({"kling": {"api_key": KLING_KEY}})

    def never(request):
        if request.method == "POST":
            return httpx.Response(200, json={"code": 0, "data": {"task_id": "k-1"}})
        return httpx.Response(200, json={"code": 0, "data": {"task_id": "k-1", "task_status": "processing"}})

    runner = ApiRunner(creds, transport=httpx.MockTransport(never), poll_interval=0.02)
    with pytest.raises(ApiError, match="可灵任务 k-1 超过"):
        runner.run(KlingEngine({"timeout": 0.01}, creds=creds).build(VideoRequest(prompt="x", first_frame=png(tmp_path / "f.png"))),
                   run_dir=tmp_path / "r", cancel=threading.Event())


# ---------------------------------------------------------------- 测试连接、记录脱敏
def test_check_connection_ark_and_kling():
    seen = []

    def h(request):
        seen.append(request)
        if "volces" in request.url.host:
            return httpx.Response(200, json={"items": [], "total": 0})
        return httpx.Response(200, json={"code": 0, "data": []})

    t = httpx.MockTransport(h)
    ok = check_connection("ark", build_creds("ark", {"api_key": ARK_KEY}), transport=t)
    assert ok["ok"] and seen[0].url.path == "/api/v3/contents/generations/tasks"
    ok = check_connection("kling", build_creds("kling", {"access_key": AK, "secret_key": SK}), transport=t)
    assert ok["ok"] and "签名" in ok["message"] and seen[1].url.path == "/v1/videos/image2video"
    verify_jwt(seen[1].headers["authorization"].removeprefix("Bearer "), SK)
    bad = check_connection("kling", build_creds("kling", {"api_key": KLING_KEY}), transport=httpx.MockTransport(
        lambda r: httpx.Response(401, json={"code": 1002, "message": f"invalid {KLING_KEY}"})))
    assert not bad["ok"] and KLING_KEY not in bad["message"] and "签名不对" in bad["message"]
    none = check_connection("kling", build_creds("kling", None))
    assert not none["ok"] and "KLING_ACCESS_KEY" in none["message"]


def test_trace_sanitize_hides_jwt_and_aksk(db):
    from backend.queue.trace import collect_secrets, sanitize

    tok = kling_jwt(AK, SK, now=1_760_000_000)
    out = json.dumps(sanitize({"note": f"Authorization {tok}", "secret_key": SK, "access_key": AK,
                               "msg": f"ak={AK}"}, [AK, SK]))
    assert tok not in out and SK not in out and AK not in out

    class Sv:
        api_keys = ApiKeyStore(db)
        cfg = None

    Sv.api_keys.update("kling", access_key=AK, secret_key=SK)
    assert {AK, SK} <= set(collect_secrets(Sv))
