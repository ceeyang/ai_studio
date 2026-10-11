"""网页文本模型（web_chatgpt / web_gemini / web_claude）：提示词拼装、页面流程（等回复写完、读全文、传图、
没登录、超时）、provider + 网关（JSON 从代码块里抠出来、看图、日志）、同一网站串行、扩展版本太旧的提示、
扩展 rpc 收发、设置页下拉、环境检查。页面用假的 ChatDriver，扩展用假的 hub 或 TestClient 的 websocket。"""
import threading
import time

import pytest
from pydantic import BaseModel

from backend.config import BrowserCfg, LLMCfg, ProviderCfg
from backend.engines.browser.chat import ExtTabDriver, ask
from backend.engines.browser.errors import BrowserError
from backend.engines.browser.extension import ExtensionHub, _Conn
from backend.engines.browser.runner import BrowserRunner
from backend.engines.browser.session import BrowserSession
from backend.engines.browser.sites import CHATGPT, CLAUDE, GEMINI
from backend.llm.base import LLMError
from backend.llm.gateway import LLMGateway
from backend.llm.web_providers import (JSON_RULE, WEB_PROVIDERS, WebChatProvider, ensure_web_providers,
                                       web_prompt, web_status)
from backend.tests.fakes.fake_browser import png

ANSWER = '好的。\n```json\n{\n  "text": "第一集：陈师傅在作坊里发现一封旧信。"\n}\n```'


# ---------------------------------------------------------------- 假页面
class FakeChatPage:
    """假的 ChatDriver：发送后先「生成中」几轮、回复一点点变长，然后停住。时间只在 sleep 时走。"""

    def __init__(self, spec, *, reply=ANSWER, busy_polls=3, login=False, never_reply=False,
                 attach_ok=True, prompt_after=0):
        self.spec, self.reply, self.busy_polls = spec, reply, busy_polls
        self.login, self.never_reply, self.attach_ok = login, never_reply, attach_ok
        self.prompt_after = prompt_after       # 跳转后这么多次 sleep 之后输入框才出来
        self.now = 0.0
        self.calls: list[tuple] = []
        self.typed = ""
        self.sent = False
        self.polls_after_send = 0
        self.attached = 0
        self.sleeps_since_goto = 0
        self.at = "https://example/"

    # 时间
    def clock(self):
        return self.now

    def sleep(self, s):
        self.now += s
        self.sleeps_since_goto += 1
        if self.sent:
            self.polls_after_send += 1

    # 页面
    def goto(self, url):
        self.calls.append(("goto", url))
        self.at = url
        self.sleeps_since_goto = 0

    def url(self):
        return self.at

    def _busy(self):
        return self.sent and not self.never_reply and self.polls_after_send < self.busy_polls

    def visible(self, sel):
        s = self.spec
        if sel == s.login_wall:
            return self.login
        if sel == s.prompt_box:
            return not self.login and self.sleeps_since_goto >= self.prompt_after
        if sel == s.stop_button:
            return self._busy()
        if sel == s.busy_marker:
            return self._busy()
        return False

    def count(self, sel):
        return self.attached if sel == self.spec.attachment else 0

    def enabled(self, sel):
        return True

    def click(self, sel):
        self.calls.append(("click", sel))
        if sel == self.spec.send_button:
            self.sent = True
        return True

    def type_text(self, sel, text):
        self.calls.append(("type", sel))
        self.typed = text
        return True

    def press(self, sel, key):
        return True

    def set_files(self, sel, files, near):
        self.calls.append(("set_files", [f["name"] for f in files]))
        if self.attach_ok:
            self.attached += len(files)
        return True

    def paste_files(self, sel, files):
        self.calls.append(("paste_files", len(files)))
        return False

    def composer_images(self, near):
        return self.attached

    def text(self, sel):
        return ""

    def reply_text(self, sel, exclude):
        assert sel == (self.spec.text_reply or self.spec.assistant_turn)
        if not self.sent or self.never_reply:
            return "", 0
        n = len(self.reply)
        part = self.reply[: max(1, n * self.polls_after_send // max(self.busy_polls, 1))]
        return (self.reply if not self._busy() else part), 1


def run_ask(page, prompt="写梗概", images=(), timeout=120.0, fresh=False):
    logs = []
    text = ask(page, page.spec, prompt, images, timeout=timeout, log=logs.append, fresh=fresh,
               clock=page.clock, poll=1.0, settle=3.0, start_timeout=30.0, report_every=2.0)
    return text, logs


# ---------------------------------------------------------------- 提示词
def test_web_prompt_puts_system_first_and_json_rule_last():
    p = web_prompt("你是编剧。", [{"role": "user", "content": "写第一集"}])
    assert p.startswith("你是编剧。") and p.index("写第一集") < p.index(JSON_RULE) and p.endswith(JSON_RULE)
    assert "```json" in JSON_RULE
    plain = web_prompt("S", [{"role": "user", "content": "U"}], json_mode=False)
    assert JSON_RULE not in plain
    retry = web_prompt("S", [{"role": "user", "content": "U"}, {"role": "assistant", "content": "坏的"},
                             {"role": "user", "content": "改一下"}])
    assert "【你上一次的回答】\n坏的" in retry and retry.index("坏的") < retry.index("改一下")


def test_ensure_web_providers_adds_three_once():
    cfg = LLMCfg(default="manual", providers=[ProviderCfg(id="manual", kind="manual")])
    ensure_web_providers(cfg)
    ensure_web_providers(cfg)
    web = [p for p in cfg.providers if p.kind == "web"]
    assert [(p.id, p.site) for p in web] == list(WEB_PROVIDERS.items())
    assert web[0].display_name() == "ChatGPT 网页（浏览器扩展，免费额度，较慢）"
    assert all(p.timeout == 300 for p in web)


# ---------------------------------------------------------------- 页面流程
@pytest.mark.parametrize("spec", [CHATGPT, GEMINI, CLAUDE], ids=lambda s: s.key)
def test_ask_waits_until_done_and_reads_full_reply(spec):
    page = FakeChatPage(spec, busy_polls=4, prompt_after=2)
    text, logs = run_ask(page)
    assert text == ANSWER                      # 生成中的半截不算，读的是写完后的全文
    assert page.calls[0] == ("goto", spec.new_chat_url)
    assert ("type", spec.prompt_box) in page.calls and ("click", spec.send_button) in page.calls
    assert page.typed == "写梗概"
    # 写完后要连续 settle 秒不变才返回（生成 4 轮 + 稳定 3 秒）
    assert page.polls_after_send >= 4 + 3
    assert any("已等" in m and "还在生成" in m for m in logs)
    assert any("回复写完" in m for m in logs)


def test_ask_fresh_tab_does_not_navigate_again():
    page = FakeChatPage(CHATGPT)
    run_ask(page, fresh=True)
    assert not [c for c in page.calls if c[0] == "goto"]


def test_ask_not_logged_in():
    page = FakeChatPage(GEMINI, login=True)
    with pytest.raises(BrowserError, match="没有登录") as e:
        run_ask(page)
    assert e.value.kind == "not_logged_in"


def test_ask_login_redirect_url():
    page = FakeChatPage(CHATGPT)
    page.goto = lambda url: setattr(page, "at", "https://auth.openai.com/log-in")
    with pytest.raises(BrowserError, match="没有登录"):
        run_ask(page)


def test_ask_never_starts():
    page = FakeChatPage(CLAUDE, never_reply=True)
    with pytest.raises(BrowserError, match="还没开始回复"):
        run_ask(page)


def test_ask_timeout_while_generating():
    page = FakeChatPage(CHATGPT, busy_polls=10_000)
    with pytest.raises(BrowserError, match="还没写完") as e:
        run_ask(page, timeout=20)
    assert e.value.kind == "timeout"
    assert ("click", CHATGPT.stop_button) in page.calls   # 超时先点停止


def test_ask_uploads_images_before_sending(tmp_path):
    p1, p2 = tmp_path / "a.png", tmp_path / "b.png"
    p1.write_bytes(png(20, 20))
    p2.write_bytes(png(20, 20))
    page = FakeChatPage(CHATGPT)
    text, logs = run_ask(page, images=[p1, p2])
    assert text == ANSWER
    names = [c for c in page.calls if c[0] == "set_files"]
    assert names == [("set_files", ["a.png", "b.png"])]
    assert page.calls.index(names[0]) < page.calls.index(("click", CHATGPT.send_button))
    assert any("图片已传上" in m for m in logs)


def test_ask_upload_fails_does_not_send(tmp_path):
    p1 = tmp_path / "a.png"
    p1.write_bytes(png(20, 20))
    page = FakeChatPage(GEMINI, attach_ok=False)
    with pytest.raises(BrowserError, match="图片没传上去"):
        run_ask(page, images=[p1])
    assert not page.sent


# ---------------------------------------------------------------- ExtTabDriver（rpc 映射）
class RecHub:
    def __init__(self, results=None):
        self.calls = []
        self.results = results or {}

    def rpc(self, method, params=None, *, timeout=90.0):
        self.calls.append((method, params))
        key = params.get("op") if method == "dom" else method
        return self.results.get(key)


def test_ext_tab_driver_maps_to_rpc():
    hub = RecHub({"replyText": {"text": "abc\ndef", "count": 2}, "visible": True, "url": "https://claude.ai/new"})
    d = ExtTabDriver(hub, 7, sleep=lambda s: None)
    assert d.reply_text("sel", "ex") == ("abc\ndef", 2)
    assert d.visible("x") is True and d.url() == "https://claude.ai/new"
    d.set_files("input", [{"name": "a.png"}], "box")
    assert hub.calls[0] == ("dom", {"tab_id": 7, "op": "replyText", "args": ["sel", "ex"]})
    assert hub.calls[-1] == ("dom", {"tab_id": 7, "op": "setFiles", "args": ["input", [{"name": "a.png"}], "box"]})
    hub.results["replyText"] = None          # 老页面 / 注入失败：不抛，当没有回复
    assert d.reply_text("sel", "") == ("", 0)


# ---------------------------------------------------------------- provider + 网关
class FakeHub:
    def __init__(self, problem=None):
        self.problem = problem
        self.connected = problem is None
        self.rpcs = []

    def text_problem(self, site, label):
        return self.problem

    def site_status(self):
        return {}

    def rpc(self, method, params=None, *, timeout=90.0):
        self.rpcs.append((method, params))
        return {"tab_id": 11, "fresh": False, "how": "用上次写文本的那个标签页"}


def make_runner(hub=None):
    runner = BrowserRunner(BrowserSession(BrowserCfg()), hub=hub or FakeHub())
    return runner


class Idea(BaseModel):
    text: str


def gateway_for(pid, provider):
    cfg = LLMCfg(default=pid, providers=[provider.cfg])
    return LLMGateway(cfg, {pid: provider})


def test_provider_through_gateway_extracts_json_and_logs(tmp_path):
    seen = {}

    def fake_ask(driver, spec, prompt, images, *, timeout, log, fresh):
        seen.update(spec=spec.key, prompt=prompt, images=images, timeout=timeout, tab=driver)
        log("假页面：在写")
        return ANSWER

    runner = make_runner()
    cfg = ProviderCfg(id="web_chatgpt", kind="web", site="chatgpt", timeout=300)
    prov = WebChatProvider(cfg, browser=runner, chat=fake_ask, driver_factory=lambda hub, tab: ("drv", tab))
    logs = []
    img = tmp_path / "ref.png"
    img.write_bytes(png(20, 20))
    out = gateway_for("web_chatgpt", prov).generate("script", "你是编剧。", "写第一集", Idea,
                                                    log=logs.append, images=[img])
    assert out.text.startswith("第一集")
    assert seen["spec"] == "chatgpt" and seen["tab"] == ("drv", 11) and seen["timeout"] == 300
    assert seen["images"] == [img] and seen["prompt"].startswith("你是编剧。") and JSON_RULE in seen["prompt"]
    assert runner.hub.rpcs == [("acquire_tab", {"site": "chatgpt"})]
    assert "假页面：在写" in logs and any("用上次写文本的那个标签页" in m for m in logs)
    assert any("ChatGPT 网页" in m and "第 1 次" in m for m in logs)


def test_provider_retries_bad_output_through_gateway():
    answers = iter(["我不太确定你要什么。", ANSWER])
    prompts = []

    def fake_ask(driver, spec, prompt, images, *, timeout, log, fresh):
        prompts.append(prompt)
        return next(answers)

    cfg = ProviderCfg(id="web_gemini", kind="web", site="gemini")
    prov = WebChatProvider(cfg, browser=make_runner(), chat=fake_ask, driver_factory=lambda h, t: None)
    out = gateway_for("web_gemini", prov).generate("script", "S", "U", Idea)
    assert out.text.startswith("第一集") and len(prompts) == 2
    assert "【你上一次的回答】\n我不太确定你要什么。" in prompts[1]


def test_provider_quota_text_becomes_clear_error():
    def fake_ask(driver, spec, prompt, images, *, timeout, log, fresh):
        return "You've reached our limit of messages. Please try again later."

    cfg = ProviderCfg(id="web_chatgpt", kind="web", site="chatgpt")
    prov = WebChatProvider(cfg, browser=make_runner(), chat=fake_ask, driver_factory=lambda h, t: None)
    with pytest.raises(LLMError, match="用量到上限"):
        prov.complete("S", [{"role": "user", "content": "U"}])


def test_provider_browser_error_becomes_llm_error():
    def fake_ask(driver, spec, prompt, images, *, timeout, log, fresh):
        raise BrowserError("not_logged_in", "Claude 网页没有登录：…")

    cfg = ProviderCfg(id="web_claude", kind="web", site="claude")
    prov = WebChatProvider(cfg, browser=make_runner(), chat=fake_ask, driver_factory=lambda h, t: None)
    with pytest.raises(LLMError, match="没有登录"):
        prov.complete("S", [{"role": "user", "content": "U"}])


def test_provider_without_browser_or_old_extension():
    cfg = ProviderCfg(id="web_claude", kind="web", site="claude")
    with pytest.raises(LLMError, match="只能在 studio 服务里用"):
        WebChatProvider(cfg).complete("S", [{"role": "user", "content": "U"}])
    prov = WebChatProvider(cfg, browser=make_runner(FakeHub(problem="浏览器扩展需要重新加载：…")))
    with pytest.raises(LLMError, match="需要重新加载"):
        prov.complete("S", [{"role": "user", "content": "U"}])
    with pytest.raises(ValueError):
        WebChatProvider(ProviderCfg(id="x", kind="web", site="bing"))


def test_same_site_is_serialized_and_shares_lock_with_image_runs():
    runner = make_runner()
    active, peak = [0], [0]
    lock = threading.Lock()

    def fake_ask(driver, spec, prompt, images, *, timeout, log, fresh):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.05)
        with lock:
            active[0] -= 1
        return ANSWER

    cfg = ProviderCfg(id="web_chatgpt", kind="web", site="chatgpt")
    prov = WebChatProvider(cfg, browser=runner, chat=fake_ask, driver_factory=lambda h, t: None)
    ts = [threading.Thread(target=prov.complete, args=("S", [{"role": "user", "content": "U"}]))
          for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(5)
    assert peak[0] == 1
    # 出图占着 ChatGPT 的锁时，文本调用排队（日志里说一声）
    logs = []
    done = threading.Event()
    with runner.site_lock("chatgpt", "ChatGPT", lambda m: None):
        t = threading.Thread(target=lambda: (prov.complete("S", [{"role": "user", "content": "U"}],
                                                           log=logs.append), done.set()))
        t.start()
        time.sleep(0.3)
        assert not done.is_set()
    t.join(5)
    assert done.is_set() and any("排队" in m for m in logs)


# ---------------------------------------------------------------- 扩展版本 / rpc
def _hub_with(hello):
    import asyncio

    hub = ExtensionHub()

    class Sock:
        async def send_json(self, msg):
            pass

    loop = asyncio.new_event_loop()
    conn = _Conn(Sock(), loop, "chrome-extension://x")
    conn.hello = hello
    hub._conn = conn
    return hub, loop


def test_text_problem_old_extension_and_missing_site():
    hub, loop = _hub_with({"version": "0.1.0", "sites": ["chatgpt", "gemini"]})
    try:
        msg = hub.text_problem("chatgpt", "ChatGPT 网页")
        assert "需要重新加载" in msg and "v0.1.0" in msg and "chrome://extensions" in msg
        with pytest.raises(BrowserError, match="需要重新加载"):
            hub.rpc("acquire_tab", {"site": "chatgpt"})
        hub._conn.hello = {"version": "0.2.0", "sites": ["chatgpt", "gemini"], "features": ["rpc"]}
        assert hub.text_problem("chatgpt", "ChatGPT 网页") is None
        assert "Claude 网页" in hub.text_problem("claude", "Claude 网页")
        hub._conn.hello["sites"].append("claude")
        assert hub.text_problem("claude", "Claude 网页") is None
    finally:
        loop.close()
    assert "没有连上" in ExtensionHub().text_problem("chatgpt", "ChatGPT 网页")


def test_web_status_reasons():
    hub, loop = _hub_with({"version": "0.1.0", "sites": ["chatgpt", "gemini"]})
    try:
        runner = make_runner(hub)
        ok, note, hint = web_status(runner, "gemini")
        assert not ok and hint == "扩展需要重新加载" and "重新加载" in note
        hub._conn.hello = {"version": "0.2.0", "sites": ["chatgpt", "gemini", "claude"], "features": ["rpc"]}
        hub._sites = {"claude": {"logged_in": False}, "chatgpt": {"logged_in": True}}
        assert web_status(runner, "chatgpt") == (True, None, None)
        ok, note, hint = web_status(runner, "claude")
        assert not ok and hint == "没登录" and "claude.ai" in note
        assert web_status(runner, "gemini")[0] is True        # 还没报状态：先当能用
    finally:
        loop.close()
    ok, note, hint = web_status(make_runner(ExtensionHub()), "chatgpt")
    assert not ok and hint == "扩展没连上"
    cdp = BrowserRunner(BrowserSession(BrowserCfg(transport="cdp")))
    assert web_status(cdp, "chatgpt")[2] == "要用浏览器扩展"


EXT = {"origin": "chrome-extension://abcdefghijklmnop"}


def test_rpc_roundtrip_over_websocket(api):
    hub = api.app.state.services.browser.hub
    with api.websocket_connect("/api/browser/ext", headers=EXT) as ws:
        ws.send_json({"type": "hello", "version": "0.2.0", "sites": ["chatgpt", "gemini", "claude"],
                      "features": ["rpc"], "browser": "Chrome 141"})
        assert ws.receive_json()["type"] == "welcome"
        end = time.monotonic() + 5
        while hub.info().get("features") != ["rpc"] and time.monotonic() < end:
            time.sleep(0.02)
        out = {}

        def call():
            try:
                out["ok"] = hub.rpc("dom", {"tab_id": 3, "op": "replyText", "args": ["a", "b"]}, timeout=5)
                hub.rpc("dom", {"tab_id": 3, "op": "evil", "args": []}, timeout=5)
            except BrowserError as e:
                out["err"] = e

        t = threading.Thread(target=call)
        t.start()
        m = ws.receive_json()
        assert m["type"] == "rpc" and m["method"] == "dom" and m["params"]["op"] == "replyText"
        ws.send_json({"type": "rpc_result", "id": m["id"], "ok": True, "result": {"text": "hi", "count": 1}})
        m2 = ws.receive_json()
        ws.send_json({"type": "rpc_result", "id": m2["id"], "ok": False, "kind": "page", "error": "不允许的页面操作 evil"})
        t.join(5)
        assert out["ok"] == {"text": "hi", "count": 1}
        assert out["err"].kind == "page" and "不允许" in str(out["err"])
        st = api.get("/api/browser/status").json()
        assert st["needs_reload"] is False and "claude" in st["sites"]


def test_settings_lists_web_models_disabled_until_extension(api):
    d = api.get("/api/settings").json()
    web = {c["id"]: c for c in d["choices"]["llm"] if c["kind"] == "web"}
    assert set(web) == set(WEB_PROVIDERS)
    assert web["web_chatgpt"]["label"] == "ChatGPT 网页（浏览器扩展，免费额度，较慢）"
    assert all(c["ready"] is False and c["hint"] == "扩展没连上" for c in web.values())
    assert "剧本、分镜" in web["web_claude"]["advice"]
    with api.websocket_connect("/api/browser/ext", headers=EXT) as ws:
        ws.send_json({"type": "hello", "version": "0.1.0", "sites": ["chatgpt", "gemini"], "browser": "Chrome"})
        ws.receive_json()
        time.sleep(0.2)
        web = {c["id"]: c for c in api.get("/api/settings").json()["choices"]["llm"] if c["kind"] == "web"}
        assert web["web_gemini"]["hint"] == "扩展需要重新加载" and "重新加载" in web["web_gemini"]["note"]
        assert api.get("/api/browser/status").json()["needs_reload"] is True
    # 设成网页文本模型能存
    r = api.put("/api/settings", json={"steps": {"episode.idea": "web_chatgpt"}})
    assert r.status_code == 200
    step = next(s for s in r.json()["steps"] if s["key"] == "episode.idea")
    assert step["value"] == "web_chatgpt" and step["effective_label"].startswith("ChatGPT 网页")


def test_doctor_reports_claude_login_and_old_extension():
    from backend.doctor import check_browser

    hub, loop = _hub_with({"version": "0.1.0", "sites": ["chatgpt", "gemini"]})
    try:
        hub._sites = {"chatgpt": {"logged_in": True, "tabs": 1}, "gemini": {"logged_in": True, "tabs": 0}}
        runner = make_runner(hub)

        class Eng:
            def __init__(self, name, site):
                self.name, self.site, self.runtime = name, site, "browser"

        class Engines:
            def all(self):
                return {"gpt_image": Eng("gpt_image", "chatgpt"), "gemini_image": Eng("gemini_image", "gemini")}

        import backend.doctor as doctor
        orig = doctor.runtime_of
        doctor.runtime_of = lambda e: getattr(e, "runtime", None) or orig(e)
        try:
            checks = {c.name: c for c in check_browser(Engines(), runner)}
        finally:
            doctor.runtime_of = orig
        assert checks["扩展版本"].status == "warn" and "重新加载" in checks["扩展版本"].detail
        assert "Claude 登录（网页文本模型）" in checks
        assert checks["Claude 登录（网页文本模型）"].status == "warn"
        assert "重新加载" in checks["Claude 登录（网页文本模型）"].detail
    finally:
        loop.close()
