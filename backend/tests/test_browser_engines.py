"""网页图像引擎：引擎拼请求、runner 落盘裁切、队列通道、首帧/定妆照走浏览器、doctor 和 API。"""
import threading
import time

import pytest
from PIL import Image

from backend.config import BrowserCfg, Config, EngineCfg, ServerCfg
from backend.doctor import check_browser
from backend.domain.models import Character, Project, Shot
from backend.domain.slots import selected_candidate
from backend.engines.base import ImageRequest
from backend.engines.browser.engines import BROWSER_FAMILIES, web_prompt
from backend.engines.browser.errors import BrowserError
from backend.engines.browser.runner import BrowserRunner, crop_to_aspect
from backend.engines.browser.session import BrowserSession, cookie_names_for, find_executable
from backend.engines.browser.sites import CHATGPT, GEMINI
from backend.engines.registry import EngineRegistry, runtime_of
from backend.pipeline.characters import run_portrait
from backend.pipeline.keyframes import run_keyframe
from backend.queue.handlers import HandlerRegistry
from backend.queue.manager import JobManager
from backend.tests.fakes.fake_browser import FakePage, png
from backend.tests.helpers import FakeCtx, FakeRunner, make_services, seed_episode

GPT_FINAL = "https://chatgpt.com/backend-api/estuary/content?id=file_final"


@pytest.fixture
def reg():
    return EngineRegistry(Config())


# ---------------------------------------------------------------- 引擎
def test_engines_registered_with_caps(reg):
    gpt, gem = reg.image("gpt_image"), reg.image("gemini_image")
    assert runtime_of(gpt) == runtime_of(gem) == "browser" and runtime_of(reg.image("zimage")) == "comfyui"
    assert (gpt.caps.max_refs, gem.caps.max_refs) == (4, 3)
    assert not gpt.caps.supports_negative and not gem.caps.supports_negative
    assert set(gpt.caps.sizes) == set(gem.caps.sizes) == {"9:16", "16:9", "1:1", "21:9"}
    assert {gpt.family, gem.family} == set(BROWSER_FAMILIES)


def test_disabled_browser_engine_not_loaded():
    r = EngineRegistry(Config(engines={"gpt_image": EngineCfg(enabled=False)}))
    assert "gpt_image" not in r.names("image") and "gemini_image" in r.names("image")


def test_build_puts_aspect_and_refs_into_prompt(reg, tmp_path):
    refs = [tmp_path / f"r{i}.png" for i in range(5)]
    call = reg.image("gpt_image").build(ImageRequest(prompt="a  mechanic\nin a workshop", aspect="9:16",
                                                     negative="blurry", refs=refs, seed=42))
    w = call.workflow
    assert call.runner == "browser" and call.output_kind == "image" and call.model_family == "chatgpt_web"
    assert w["site"] == "chatgpt" and w["aspect"] == "9:16" and w["timeout"] == 420
    assert w["refs"] == [str(p) for p in refs[:4]] and call.dropped_refs == [str(refs[4])]
    assert "\n" not in w["prompt"] and "9:16" in w["prompt"] and "1080×1920" in w["prompt"]
    assert "4 images are references" in w["prompt"] and w["prompt"].endswith("a mechanic in a workshop")
    assert call.meta["seed"] is None and call.meta["nondeterministic"] and call.meta["negative_ignored"] == "blurry"
    gem = reg.image("gemini_image").build(ImageRequest(prompt="x", aspect="21:9", refs=refs))
    assert len(gem.workflow["refs"]) == 3 and "21:9" in gem.workflow["prompt"]
    with pytest.raises(ValueError, match="不支持画幅"):
        reg.image("gpt_image").build(ImageRequest(prompt="x", aspect="4:3"))


def test_engine_params_override_timeout_and_fit():
    r = EngineRegistry(Config(engines={"gemini_image": EngineCfg(params={"timeout": 99, "fit_aspect": False})}))
    w = r.image("gemini_image").build(ImageRequest(prompt="x")).workflow
    assert w["timeout"] == 99 and w["fit_aspect"] is False


def test_web_prompt_without_refs():
    t = web_prompt("p", "1:1", 0)
    assert "reference" not in t and "square 1:1" in t


# ---------------------------------------------------------------- runner
def test_crop_to_aspect():
    im = Image.new("RGB", (1024, 1536))
    c = crop_to_aspect(im, "9:16")
    assert c.size == (864, 1536)
    assert crop_to_aspect(Image.new("RGB", (1080, 1920)), "9:16") is None
    assert crop_to_aspect(Image.new("RGB", (1024, 1024)), "16:9").size == (1024, 576)


def runner_with(page, tmp_path):
    return BrowserRunner(BrowserSession(BrowserCfg(transport="cdp", user_data_dir=tmp_path / "profile")),
                         page_factory=page.factory, poll=1.0)   # 假页面的 sleep 只推进假时钟


def gpt_page(w=1024, h=1536):
    page = FakePage(CHATGPT)
    page.after_submit([(1, lambda p: p.show(CHATGPT.stop_button)),
                       (30, lambda p: p.finish(GPT_FINAL, w, h))])
    return page


def patch_clock(monkeypatch, page):
    from backend.engines.browser import flow
    real = flow.generate

    def gen(*a, **kw):
        kw["clock"] = page.clock
        return real(*a, **kw)
    monkeypatch.setattr(flow, "generate", gen)


def test_runner_saves_and_crops(tmp_path, reg, monkeypatch):
    page = gpt_page()
    patch_clock(monkeypatch, page)
    call = reg.image("gpt_image").build(ImageRequest(prompt="p", aspect="9:16"))
    logs = []
    files = runner_with(page, tmp_path).run(call, run_dir=tmp_path / "run", cancel=threading.Event(),
                                            log=logs.append)
    assert [f.name for f in files] == ["chatgpt_00.png"]
    assert Image.open(files[0]).size == (864, 1536)
    assert (tmp_path / "run" / "chatgpt_00_orig.png").exists()
    assert (tmp_path / "run" / "workflow.api.json").exists() and (tmp_path / "run" / "call.json").exists()
    assert any("居中裁成 864×1536" in m for m in logs)


def test_runner_keeps_original_when_aspect_ok(tmp_path, reg, monkeypatch):
    page = gpt_page(1024, 1024)
    patch_clock(monkeypatch, page)
    call = reg.image("gpt_image").build(ImageRequest(prompt="p", aspect="1:1"))
    files = runner_with(page, tmp_path).run(call, run_dir=tmp_path / "run", cancel=threading.Event())
    assert [f.name for f in files] == ["chatgpt_00_orig.png"]


def test_runner_one_generation_per_site(tmp_path, reg):
    active, peak = [0], [0]
    gate = threading.Lock()

    class SlowPage(FakePage):
        def goto(self, url):
            with gate:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            time.sleep(0.05)
            with gate:
                active[0] -= 1
            raise BrowserError("page", "停在这里")

    page = SlowPage(CHATGPT)
    runner = runner_with(page, tmp_path)
    call = reg.image("gpt_image").build(ImageRequest(prompt="p"))
    errs = []

    def go(i):
        try:
            runner.run(call, run_dir=tmp_path / f"r{i}", cancel=threading.Event())
        except BrowserError as e:
            errs.append(e)

    ts = [threading.Thread(target=go, args=(i,)) for i in range(3)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(errs) == 3 and peak[0] == 1


def test_login_status_from_cookies(tmp_path, monkeypatch):
    runner = BrowserRunner(BrowserSession(BrowserCfg(transport="cdp")))
    monkeypatch.setattr(runner.session, "cookies", lambda: [
        {"name": "__Secure-1PSID", "domain": ".google.com"},
        {"name": "other", "domain": "chatgpt.com"}])
    assert runner.login_status() == {"chatgpt": False, "gemini": True}
    assert cookie_names_for([{"name": "a", "domain": ".google.com"}], "gemini.google.com") == {"a"}
    assert cookie_names_for([{"name": "a", "domain": "evilgoogle.com"}], "google.com") == set()


def test_find_executable_prefers_config(tmp_path):
    exe = tmp_path / "chrome.exe"
    exe.write_bytes(b"")
    assert find_executable(BrowserCfg(executable=exe)) == exe
    assert find_executable(BrowserCfg(executable=tmp_path / "missing.exe")) is None


def test_launch_args_use_own_profile_and_port(tmp_path):
    exe = tmp_path / "chrome.exe"
    exe.write_bytes(b"")
    s = BrowserSession(BrowserCfg(executable=exe, user_data_dir=tmp_path / "prof", cdp_port=9444))
    args = s.launch_args(["https://chatgpt.com/"])
    assert args[0] == str(exe) and "--remote-debugging-port=9444" in args
    assert f"--user-data-dir={(tmp_path / 'prof').resolve()}" in args
    assert "--headless=new" not in args and args[-1] == "https://chatgpt.com/"
    with pytest.raises(BrowserError, match="找不到 Chrome 或 Edge"):
        BrowserSession(BrowserCfg(executable=tmp_path / "nope.exe")).launch_args()


# ---------------------------------------------------------------- 队列通道
def test_browser_jobs_run_on_their_own_lane(db):
    reg = HandlerRegistry()
    seen = []

    @reg.register("img", gpu=True)
    def img(ctx, payload):
        seen.append((payload["n"], threading.current_thread().name))

    m = JobManager(db, reg, poll=0.02)
    m.lanes = BROWSER_FAMILIES
    switches, handovers = [], []
    m.on_family_switch = lambda a, b: switches.append((a, b))
    m.on_gpu_handover = lambda: handovers.append(1)
    a = m.submit("img", {"n": "gpt"}, model_family="chatgpt_web")
    b = m.submit("img", {"n": "gem"}, model_family="gemini_web")
    c = m.submit("img", {"n": "h3"}, model_family="h3")
    assert (m.get(a)["gpu"], m.get(b)["gpu"], m.get(c)["gpu"]) == (False, False, True)
    # CPU worker 不领通道里的任务
    assert m._claim(gpu=False) is None
    m.start()
    try:
        end = time.monotonic() + 5
        while time.monotonic() < end and any(m.get(j)["status"] != "done" for j in (a, b, c)):
            time.sleep(0.02)
        threads = dict(seen)
        assert threads == {"gpt": "lane-chatgpt_web", "gem": "lane-gemini_web", "h3": "gpu-worker"}
        assert switches == [] and handovers == [] and m.current_family == "h3"
    finally:
        m.stop()


# ---------------------------------------------------------------- 首帧 / 定妆照走浏览器
class FakeBrowserRunner:
    def __init__(self):
        self.calls = []

    def run(self, call, *, run_dir, cancel, on_progress=None, log=None):
        self.calls.append(call)
        run_dir.mkdir(parents=True, exist_ok=True)
        f = run_dir / "chatgpt_00.png"
        f.write_bytes(png(90, 160))
        if log:
            log("ChatGPT：假的出图")
        return [f]


def test_keyframe_with_gpt_image_uses_browser_runner(tmp_path, db):
    comfy = FakeRunner()
    sv = make_services(tmp_path, db, comfy=comfy)
    sv.browser = FakeBrowserRunner()
    proj_id, ep_id = seed_episode(db, image_engine="gpt_image")
    with db.session() as s:
        sh = Shot(episode_id=ep_id, order=0, code="S01", image_prompt="a mechanic")
        s.add(sh)
        s.flush()
        shot_id = sh.id
    ctx = FakeCtx(sv, job_id=5)
    res = run_keyframe(ctx, {"shot_id": shot_id})
    assert comfy.calls == [] and len(sv.browser.calls) == 1
    assert sv.browser.calls[0].workflow["aspect"] == "9:16"
    assert any("无种子" in m for m in ctx.logs) and any("假的出图" in m for m in ctx.logs)
    with db.session() as s:
        c = selected_candidate(s, "shot", shot_id, "first_frame")
        assert c.id == res["candidate_ids"][0] and c.engine == "gpt_image"
        assert c.params["seed"] is None and c.params["workflow"] == "demo/runs/5/workflow.api.json"
    # 输入没变：不重出（哈希不含种子，网页出图不确定也不影响过期判断）
    assert run_keyframe(ctx, {"shot_id": shot_id}) == {"skipped": True, "candidate_ids": []}


def test_portrait_engine_override(tmp_path, db):
    sv = make_services(tmp_path, db, comfy=FakeRunner())
    sv.browser = FakeBrowserRunner()
    proj_id, _ = seed_episode(db, image_engine="zimage")
    with db.session() as s:
        ch = Character(project_id=proj_id, name="陈师傅", visual_prompt="LOOK")
        s.add(ch)
        s.flush()
        cid = ch.id
    run_portrait(FakeCtx(sv, job_id=3), {"character_id": cid, "engine": "gemini_image"})
    assert sv.browser.calls[0].workflow["site"] == "gemini"
    assert sv.comfy.calls == []


# ---------------------------------------------------------------- doctor
class FakeSession:
    def __init__(self, tmp_path, *, exe=True, running=True, taken=False):
        self.cfg = BrowserCfg(user_data_dir=tmp_path / "profile")
        self.endpoint = "http://127.0.0.1:9333"
        self._exe, self._running, self._taken = exe, running, taken

    def executable(self):
        return "C:/chrome.exe" if self._exe else None

    def version(self):
        return {"Browser": "Chrome/140.0"} if self._running else None

    def port_taken(self):
        return self._taken


class FakeLoginRunner:
    sites = {"chatgpt": CHATGPT, "gemini": GEMINI}

    def __init__(self, session, status):
        self.session, self.status = session, status

    def login_status(self):
        return self.status


def checks(reg, runner):
    return {c.name: c for c in check_browser(reg, runner)}


def test_doctor_browser_logged_in_and_not(tmp_path, reg):
    c = checks(reg, FakeLoginRunner(FakeSession(tmp_path), {"chatgpt": True, "gemini": False}))
    assert c["程序"].status == "ok" and c["调试端口"].status == "ok" and "Chrome/140" in c["调试端口"].detail
    assert c["ChatGPT 登录（gpt_image）"].status == "ok"
    g = c["Gemini 登录（gemini_image）"]
    assert g.status == "warn" and "在设置页点“打开浏览器登录”" in g.detail


def test_doctor_browser_not_running_or_missing(tmp_path, reg):
    c = checks(reg, FakeLoginRunner(FakeSession(tmp_path, running=False), {}))
    assert c["调试端口"].status == "ok" and "自动启动" in c["调试端口"].detail
    assert c["ChatGPT 登录（gpt_image）"].status == "warn" and "打开浏览器登录" in c["ChatGPT 登录（gpt_image）"].detail
    c = checks(reg, FakeLoginRunner(FakeSession(tmp_path, exe=False, running=False), {}))
    assert c["程序"].status == "fail" and c["调试端口"].status == "fail"
    c = checks(reg, FakeLoginRunner(FakeSession(tmp_path, running=False, taken=True), {}))
    assert c["调试端口"].status == "fail" and "占用" in c["调试端口"].detail


def test_doctor_skips_browser_when_engines_disabled(tmp_path):
    r = EngineRegistry(Config(engines={"gpt_image": EngineCfg(enabled=False),
                                       "gemini_image": EngineCfg(enabled=False)}))
    assert check_browser(r, FakeLoginRunner(FakeSession(tmp_path), {})) == []


# ---------------------------------------------------------------- API
def test_api_engines_and_browser_routes(api, monkeypatch):
    rows = {e["name"]: e for e in api.get("/api/engines").json()["image"]}
    assert rows["gpt_image"]["runtime"] == "browser" and rows["gpt_image"]["family"] == "chatgpt_web"
    assert rows["zimage"]["runtime"] == "comfyui"
    runner = api.app.state.services.browser
    monkeypatch.setattr(runner.session.cfg, "transport", "cdp")   # 备用的 cdp 方式（默认是扩展）
    opened = []
    monkeypatch.setattr(runner.session, "open_login", lambda urls: opened.append(urls) or True)
    r = api.post("/api/browser/login")
    assert r.status_code == 200 and r.json()["launched"] is True
    assert opened == [["https://chatgpt.com/", "https://gemini.google.com/app"]]
    monkeypatch.setattr(runner.session, "version", lambda: None)
    st = api.get("/api/browser/status").json()
    assert st["running"] is False and st["logged_in"] == {"chatgpt": None, "gemini": None}


def test_api_portrait_draw_with_engine(api):
    api.post("/api/projects", json={"title": "demo"})
    ch = api.post("/api/projects/demo/characters", json={"name": "陈师傅", "visual_prompt": "LOOK"}).json()
    r = api.post(f"/api/characters/{ch['id']}/portraits/draw", json={"count": 1, "engine": "gpt_image"})
    assert r.status_code == 200, r.text
    job = api.get(f"/api/jobs/{r.json()['job_ids'][0]}").json()
    assert job["payload"]["engine"] == "gpt_image" and job["model_family"] == "chatgpt_web"
    assert job["gpu"] is False
    r = api.post(f"/api/characters/{ch['id']}/portraits/draw", json={"count": 1, "engine": "nope"})
    assert r.status_code == 400
