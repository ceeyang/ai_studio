"""网页会话续接：作用域映射、对话地址识别、续接 / 新开的判断和记录、轮次上限、页面流程（只取新增的图和回复、
会话打不开时退回新对话）、网页出图（扩展 v0.3 续接、旧扩展退回新对话）、网页文本模型按集按步骤续接、接口。"""
import threading

import pytest

from backend.config import BrowserCfg, ProviderCfg
from backend.domain.models import BrowserConversation, Job
from backend.domain.settings import SettingsStore
from backend.engines.base import ImageRequest
from backend.engines.browser import flow
from backend.engines.browser.chat import ask
from backend.engines.browser.conversations import (CONTINUE_IMAGE_PREFIX, CONTINUE_TEXT_PREFIX, MAX_TURNS,
                                                   ConversationBook, Plan, Scope, conversation_id, describe,
                                                   scope_for_job, use_scope)
from backend.engines.browser.runner import BrowserRunner
from backend.engines.browser.session import BrowserSession
from backend.engines.browser.sites import CHATGPT, CLAUDE, GEMINI
from backend.llm.web_providers import WebChatProvider
from backend.queue.trace import JobTracer, list_traces, use_tracer
from backend.tests.fakes.fake_browser import FakePage, png
from backend.tests.test_browser_extension import EXT, hello, send_image, wait_for
from backend.tests.test_web_llm import ANSWER, FakeChatPage, Idea, gateway_for, make_runner

CONV_A = "https://chatgpt.com/c/68e9a1b2-0001-8000-aaaa-000000000001"
CONV_B = "https://chatgpt.com/c/68e9a1b2-0002-8000-bbbb-000000000002"
GPT_OLD = "blob:https://chatgpt.com/old-1"
GPT_NEW = "blob:https://chatgpt.com/new-1"


# ---------------------------------------------------------------- 作用域、地址
def test_scope_for_job_mapping():
    assert scope_for_job("image.portrait", {"character_id": 3}, 1) == Scope("character", "3", 1)
    assert scope_for_job("image.turnaround", {"character_id": 3, "sheet": "body"}, 1).key == "character:3"
    assert scope_for_job("image.keyframe", {"shot_id": 9}).key == "shot:9"
    assert scope_for_job("image.location", {"location_id": 4}).key == "location:4"
    assert scope_for_job("image.prop", {"prop_id": 5, "new_conversation": True}) == Scope("prop", "5", None, True)
    sb = scope_for_job("storyboard", {"episode_id": 7}, 2)
    assert sb.key == "episode:7" and sb.for_text and not sb.for_images
    assert sb.child("storyboard:12").key == "episode:7:storyboard:12"
    assert scope_for_job("script", {"episode_id": 7}).child("script_structure").id == "7:script_structure"
    assert scope_for_job("character.recommend", {"episode_id": 7}).key == "episode:7"
    # 没归属的（写梗概、推荐造型、配音……）不续接
    for t in ("episode.idea", "character.suggest", "post.dub", "video.shot", "export.episode"):
        assert scope_for_job(t, {"episode_id": 7, "character_id": 1}) is None
    assert scope_for_job("image.portrait", {}) is None


@pytest.mark.parametrize("spec,url,want", [
    (CHATGPT, CONV_A, "68e9a1b2-0001-8000-aaaa-000000000001"),
    (CHATGPT, "https://chatgpt.com/g/g-abc/c/68e9a1b2-0003", "68e9a1b2-0003"),
    (CHATGPT, "https://chatgpt.com/", ""),
    (CHATGPT, "https://auth.openai.com/log-in", ""),
    (CHATGPT, "https://evil.example/c/68e9a1b2-0001", ""),
    (GEMINI, "https://gemini.google.com/app/1a2b3c4d5e6f7a8b", "1a2b3c4d5e6f7a8b"),
    (GEMINI, "https://gemini.google.com/u/1/app/1a2b3c4d5e6f", "1a2b3c4d5e6f"),
    (GEMINI, "https://gemini.google.com/app", ""),
    (CLAUDE, "https://claude.ai/chat/0b1c2d3e-aaaa-bbbb-cccc-000000000001", "0b1c2d3e-aaaa-bbbb-cccc-000000000001"),
    (CLAUDE, "https://claude.ai/new", ""),
    (CHATGPT, "", ""),
])
def test_conversation_id(spec, url, want):
    assert conversation_id(spec, url) == want


# ---------------------------------------------------------------- 续接 / 新开的判断和记录
def test_book_plan_and_record(db):
    store = SettingsStore(db)
    book = ConversationBook(db, store)
    sc = Scope("character", "3", 1)
    p = book.plan("chatgpt", sc)
    assert not p.continuing and "还没有网页会话" in p.reason
    info = book.record(p, CHATGPT, CONV_A)
    assert info == {"mode": "new", "url": CONV_A, "reason": p.reason, "site": "chatgpt",
                    "scope": "character:3", "turns": 1}
    p = book.plan("chatgpt", sc)
    assert p.continuing and p.url == CONV_A and p.turns == 1
    info = book.record(p, CHATGPT, CONV_A, continued=True)
    assert info["mode"] == "continue" and info["turns"] == 2 and info["reason"] is None
    assert book.get("chatgpt", sc)["turns"] == 2
    # 别的网站、别的角色各自一条
    assert not book.plan("gemini", sc).continuing
    assert not book.plan("chatgpt", Scope("character", "4")).continuing
    # 勾了「新开对话」：不续接；记下新的那条（轮数从 1 算）
    p = book.plan("chatgpt", Scope("character", "3", 1, fresh=True))
    assert not p.continuing and "新开对话" in p.reason
    book.record(p, CHATGPT, CONV_B)
    row = book.get("chatgpt", sc)
    assert row["url"] == CONV_B and row["turns"] == 1 and row["project_id"] == 1
    # 设置里关掉：不续接
    store.save({"web_reuse_conversation": False})
    assert store.load().web_reuse_conversation is False
    p = book.plan("chatgpt", sc)
    assert not p.continuing and "设置里关了" in p.reason
    store.save({"web_reuse_conversation": True})
    assert book.plan("chatgpt", sc).continuing
    assert [r["site"] for r in book.list("character", "3")] == ["chatgpt"]


def test_book_turn_limit_and_fallbacks(db):
    book = ConversationBook(db, max_turns=3)
    sc = Scope("shot", "9")
    book.record(book.plan("chatgpt", sc), CHATGPT, CONV_A)
    for _ in range(2):
        book.record(book.plan("chatgpt", sc), CHATGPT, CONV_A, continued=True)
    assert book.get("chatgpt", sc)["turns"] == 3
    p = book.plan("chatgpt", sc)
    assert not p.continuing and "3 轮" in p.reason and "上限 3" in p.reason
    # 续接时页面流程说那条对话打不开、退回了新对话：按新会话记，原因写明
    book2 = ConversationBook(db)
    sc2 = Scope("prop", "5")
    book2.record(book2.plan("chatgpt", sc2), CHATGPT, CONV_A)
    p = book2.plan("chatgpt", sc2)
    info = book2.record(p, CHATGPT, CONV_B, continued=False, note="会话打不开（跳到了 https://chatgpt.com/）")
    assert info["mode"] == "new" and "打不开" in info["reason"]
    assert book2.get("chatgpt", sc2)["url"] == CONV_B and book2.get("chatgpt", sc2)["turns"] == 1
    # 说续接上了但地址变了：按新会话记
    p = book2.plan("chatgpt", sc2)
    info = book2.record(p, CHATGPT, CONV_A, continued=True)
    assert info["mode"] == "new" and "地址变了" in info["reason"]
    # 没认出对话地址：不记，说明一句
    p = book2.plan("chatgpt", Scope("prop", "6"))
    info = book2.record(p, CHATGPT, "https://chatgpt.com/")
    assert "没有记下" in info["note"] and book2.get("chatgpt", Scope("prop", "6")) is None
    assert "新开会话" in describe(info, "ChatGPT")
    assert "续接会话 " + CONV_A in describe({"mode": "continue", "url": CONV_A, "turns": 2}, "ChatGPT")


def test_book_write_waits_for_open_session(db):
    """在没提交的写事务里调用（处理器常这样）：等会话结束再写，不卡写锁。"""
    book = ConversationBook(db)
    sc = Scope("location", "4")
    with db.session() as s:
        s.add(Job(type="x"))
        s.flush()
        book.record(book.plan("chatgpt", sc), CHATGPT, CONV_A)
        assert db.in_session()
    assert book.get("chatgpt", sc)["url"] == CONV_A


# ---------------------------------------------------------------- 出图页面流程（cdp 的 flow.py）
def old_conversation_page(url=CONV_A):
    """一条已有的对话：页面上有上一轮的图（alt 和尺寸都和新图一样）和上一轮的回复。"""
    page = FakePage(CHATGPT)
    page.url_now = "about:blank"
    page.add_result(GPT_OLD, 1024, 1536, alt="Generated image 1")
    page.reply = "Here is the image you asked for."
    page.show(CHATGPT.assistant_turn)

    def done(p):
        p.hide(CHATGPT.stop_button)
        p.add_result(GPT_NEW, 1024, 1536, alt="Generated image 1")
        p.reply = "Here is the new image."
    page.after_submit([(1, lambda p: p.show(CHATGPT.stop_button)), (40, done)])
    return page


def run_flow(page, conversation_url=None):
    logs, info = [], {}
    out = flow.generate(page, CHATGPT, "PROMPT", [], timeout=300, log=logs.append, clock=page.clock,
                        poll=1.0, conversation_url=conversation_url, info=info)
    return out, logs, info


def test_flow_continues_and_takes_only_new_image():
    page = old_conversation_page()
    out, logs, info = run_flow(page, CONV_A)
    assert [o.src for o in out] == [GPT_NEW]          # 旧图（alt、尺寸一样）不算，新图也没被当成旧图的副本
    assert ("goto", CONV_A) in page.calls and ("goto", CHATGPT.new_chat_url) not in page.calls
    assert ("fetch", GPT_OLD) not in page.calls
    assert info == {"url": CONV_A, "continued": True, "note": ""}
    assert any("打开之前的会话" in m for m in logs) and not any("打开新对话" in m for m in logs)


def test_flow_continue_old_reply_is_not_mistaken_for_no_image():
    """续接时上一轮的回复文字还在页面上：不能当成「回复了文字没出图」提前失败。"""
    page = old_conversation_page()
    page.after_submit([(1, lambda p: p.show(CHATGPT.stop_button)), (2, lambda p: p.hide(CHATGPT.stop_button)),
                       (60, lambda p: p.add_result(GPT_NEW, 1024, 1536))])
    out, _, _ = run_flow(page, CONV_A)
    assert [o.src for o in out] == [GPT_NEW]


def test_flow_conversation_gone_falls_back_to_new_chat():
    page = old_conversation_page()
    real_goto = page.goto

    def goto(url):
        real_goto(url)
        if url == CONV_A:               # 对话被删了：网站跳回首页
            page.url_now = "https://chatgpt.com/"
    page.goto = goto
    out, logs, info = run_flow(page, CONV_A)
    assert ("goto", CHATGPT.new_chat_url) in page.calls
    assert info["continued"] is False and "会话打不开" in info["note"]
    assert any("新开对话" in m for m in logs)


def test_flow_empty_conversation_falls_back():
    page = FakePage(CHATGPT).after_submit([(1, lambda p: p.show(CHATGPT.stop_button)),
                                           (30, lambda p: p.finish(GPT_NEW))])
    out, _, info = run_flow(page, CONV_A)     # 页面上一条回复都没有
    assert info["continued"] is False and "看不到之前的回复" in info["note"]
    assert [o.src for o in out] == [GPT_NEW]


# ---------------------------------------------------------------- 文本页面流程（chat.py）
class ConvChatPage(FakeChatPage):
    """已有一条回复的对话：reply_text 发送前给旧回复（1 条），发送后新回复写出来才变成 2 条。"""

    def __init__(self, spec, *, old="上一次的回答", new=ANSWER, gone=False, **kw):
        super().__init__(spec, reply=new, **kw)
        self.old, self.gone = old, gone

    def goto(self, url):
        super().goto(url)
        if self.gone and "/c/" in url:
            self.at = "https://chatgpt.com/"

    def reply_text(self, sel, exclude):
        if "/c/" not in self.at and not self.sent:
            return "", 0
        if not self.sent or self.polls_after_send < 2:   # 发出去后两轮还看不到新回复：只有旧的
            return self.old, 1
        n = len(self.reply)
        part = self.reply[: max(1, n * self.polls_after_send // max(self.busy_polls, 1))]
        return (self.reply if not self._busy() else part), 2


def run_conv_ask(page, url):
    logs, info = [], {}
    text = ask(page, page.spec, "写分镜", (), timeout=120.0, log=logs.append, clock=page.clock, poll=1.0,
               settle=3.0, start_timeout=30.0, report_every=50.0, conversation_url=url, info=info,
               continue_prefix=CONTINUE_TEXT_PREFIX)
    return text, logs, info


def test_ask_continues_and_reads_only_new_reply():
    page = ConvChatPage(CHATGPT, busy_polls=4)
    text, logs, info = run_conv_ask(page, CONV_A)
    assert text == ANSWER                          # 不是「上一次的回答」
    assert ("goto", CONV_A) in page.calls and ("goto", CHATGPT.new_chat_url) not in page.calls
    assert page.typed.startswith(CONTINUE_TEXT_PREFIX) and page.typed.endswith("写分镜")
    assert info == {"url": CONV_A, "continued": True, "note": ""}


def test_ask_conversation_gone_opens_new_chat():
    page = ConvChatPage(CHATGPT, gone=True)
    page.reply_text = lambda sel, ex: (("", 0) if not page.sent else (ANSWER, 1))
    text, logs, info = run_conv_ask(page, CONV_A)
    assert text == ANSWER and info["continued"] is False and "会话打不开" in info["note"]
    assert ("goto", CHATGPT.new_chat_url) in page.calls and page.typed == "写分镜"


# ---------------------------------------------------------------- 网页出图：runner + 扩展
class ImageJob:
    """在线程里（带作用域）跑 runner.run，测试线程扮演扩展。"""

    def __init__(self, api, scope, tmp_path, name="run"):
        sv = api.app.state.services
        self.runner = sv.browser
        self.call = sv.engines.image("gpt_image").build(ImageRequest(prompt="陈师傅 portrait", aspect="9:16"))
        self.run_dir = tmp_path / name
        self.logs, self.files, self.error = [], None, None
        self.scope = scope
        self.t = threading.Thread(target=self._go, daemon=True)
        self.t.start()

    def _go(self):
        try:
            with use_scope(self.scope):
                self.files = self.runner.run(self.call, run_dir=self.run_dir, cancel=threading.Event(),
                                             log=self.logs.append)
        except BaseException as e:   # noqa: BLE001
            self.error = e

    def join(self):
        self.t.join(10)
        assert not self.t.is_alive()
        return self


def recv_job(ws):
    while True:   # 跳过 status_request 之类
        msg = ws.receive_json()
        if msg.get("type") == "job":
            return msg


def answer(ws, jid, src, conversation=None):
    send_image(ws, jid, png(1024, 1536), src=src)
    msg = {"type": "result", "job_id": jid, "count": 1}
    if conversation is not None:
        msg["conversation"] = conversation
    ws.send_json(msg)


def test_extension_v03_continues_same_character_conversation(api, tmp_path):
    book = api.app.state.services.browser.conversations
    sc = Scope("character", "3", None)
    with api.websocket_connect("/api/browser/ext", headers=EXT) as ws:
        hello(ws, version="0.3.0", features=["rpc", "conversation"])
        job = ImageJob(api, sc, tmp_path, "r1")
        msg = recv_job(ws)
        assert "conversation_url" not in msg and not msg["prompt"].startswith(CONTINUE_IMAGE_PREFIX)
        answer(ws, msg["job_id"], GPT_NEW, {"url": CONV_A, "continued": False, "note": ""})
        job.join()
        assert job.error is None, job.error
        assert any("新开会话 " + CONV_A in m for m in job.logs)
        assert wait_for(lambda: book.get("chatgpt", sc) is not None)
        # 第二次：同一个角色、同一个网站 → 续接那条对话，提示词开头说明是一张新图
        job = ImageJob(api, sc, tmp_path, "r2")
        msg = recv_job(ws)
        assert msg["conversation_url"] == CONV_A
        assert msg["prompt"].startswith(CONTINUE_IMAGE_PREFIX) and "陈师傅 portrait" in msg["prompt"]
        answer(ws, msg["job_id"], GPT_NEW + "2", {"url": CONV_A, "continued": True, "note": ""})
        job.join()
        assert job.error is None, job.error
        assert any("续接会话 " + CONV_A in m and "第 2 轮" in m for m in job.logs)
        assert book.get("chatgpt", sc)["turns"] == 2
        # 别的角色：新开
        job = ImageJob(api, Scope("character", "4"), tmp_path, "r3")
        msg = recv_job(ws)
        assert "conversation_url" not in msg
        answer(ws, msg["job_id"], GPT_NEW + "3", {"url": CONV_B, "continued": False, "note": ""})
        job.join()
    r = api.get("/api/browser/conversations", params={"scope_kind": "character", "scope_id": "3"}).json()
    assert r["reuse_enabled"] is True and r["max_turns"] == MAX_TURNS
    assert r["items"] == [{"site": "chatgpt", "label": "ChatGPT", "url": CONV_A, "turns": 2,
                           "updated_at": r["items"][0]["updated_at"]}]


def test_old_extension_falls_back_to_new_conversation_with_hint(api, tmp_path):
    book = api.app.state.services.browser.conversations
    sc = Scope("shot", "9")
    book.record(book.plan("chatgpt", sc), CHATGPT, CONV_A)
    with api.websocket_connect("/api/browser/ext", headers=EXT) as ws:
        hello(ws, version="0.2.0", features=["rpc"])
        st = api.get("/api/browser/status").json()
        assert st["conversation"]["supported"] is False and "重新加载" in st["conversation"]["hint"]
        job = ImageJob(api, sc, tmp_path)
        msg = recv_job(ws)
        assert "conversation_url" not in msg and not msg["prompt"].startswith(CONTINUE_IMAGE_PREFIX)
        answer(ws, msg["job_id"], GPT_NEW)
        job.join()
        r = api.get("/api/browser/conversations", params={"scope_kind": "shot", "scope_id": "9"}).json()
        assert r["supported"] is False and "重新加载" in r["hint"]
    assert job.error is None, job.error
    assert any("v0.2.0 不支持续接" in m and "重新加载" in m for m in job.logs)
    assert book.get("chatgpt", sc)["turns"] == 1     # 没续接、也没改记录


def test_no_scope_no_conversation_handling(api, tmp_path):
    with api.websocket_connect("/api/browser/ext", headers=EXT) as ws:
        hello(ws, version="0.3.0", features=["rpc", "conversation"])
        job = ImageJob(api, None, tmp_path)
        msg = recv_job(ws)
        assert "conversation_url" not in msg
        answer(ws, msg["job_id"], GPT_NEW, {"url": CONV_A, "continued": False, "note": ""})
        job.join()
    assert job.error is None and not any("会话" in m for m in job.logs)


def test_cdp_runner_continues_and_traces(tmp_path, db):
    page = old_conversation_page()
    runner = BrowserRunner(BrowserSession(BrowserCfg(transport="cdp", user_data_dir=tmp_path / "p")),
                           page_factory=page.factory, poll=1.0)
    runner.conversations = ConversationBook(db)
    sc = Scope("prop", "5")
    runner.conversations.record(runner.conversations.plan("chatgpt", sc), CHATGPT, CONV_A)
    import backend.engines.browser.flow as flow_mod
    real = flow_mod.generate

    def gen(*a, **kw):
        kw["clock"] = page.clock
        return real(*a, **kw)
    flow_mod.generate = gen
    try:
        from backend.engines.registry import EngineRegistry
        from backend.config import Config
        call = EngineRegistry(Config()).image("gpt_image").build(ImageRequest(prompt="a wrench", aspect="9:16"))
        db.session  # noqa: B018
        with db.session() as s:
            s.add(Job(type="image.prop"))
        tracer = JobTracer(db, 1)
        logs = []
        with use_tracer(tracer), use_scope(sc):
            tracer.add("engine", "出图 · gpt_image", {"prompt": "x"})
            files = runner.run(call, run_dir=tmp_path / "run", cancel=threading.Event(), log=logs.append)
    finally:
        flow_mod.generate = real
    assert len(files) == 1 and ("goto", CONV_A) in page.calls
    assert page.typed.startswith(CONTINUE_IMAGE_PREFIX)
    assert any("续接会话" in m for m in logs)
    conv = list_traces(db, 1)[0]["data"]["conversation"]
    assert conv["mode"] == "continue" and conv["url"] == CONV_A and conv["turns"] == 2


# ---------------------------------------------------------------- 网页文本模型
def test_web_text_provider_continues_per_episode_step(db):
    runner = make_runner()
    runner.conversations = ConversationBook(db)
    seen = []
    urls = iter([CONV_A, CONV_A, CONV_B])

    def fake_ask(driver, spec, prompt, images, *, timeout, log, fresh, conversation_url=None, info=None,
                 continue_prefix=""):
        seen.append(conversation_url)
        url = next(urls)
        info.update(url=url, continued=bool(conversation_url), note="")
        return ANSWER

    cfg = ProviderCfg(id="web_chatgpt", kind="web", site="chatgpt")
    prov = WebChatProvider(cfg, browser=runner, chat=fake_ask, driver_factory=lambda h, t: None)
    gw = gateway_for("web_chatgpt", prov)
    logs = []
    ep = Scope("episode", "7", 1)
    with use_scope(ep):
        gw.generate("storyboard", "S", "U", Idea, key="storyboard:12", log=logs.append)
        gw.generate("storyboard", "S", "U", Idea, key="storyboard:12", log=logs.append)
        gw.generate("storyboard", "S", "U", Idea, key="storyboard:13", log=logs.append)   # 另一段：新开
    assert seen == [None, CONV_A, None]
    book = runner.conversations
    assert book.get("chatgpt", ep.child("storyboard:12"))["turns"] == 2
    assert book.get("chatgpt", ep.child("storyboard:13"))["url"] == CONV_B
    assert any("续接会话 " + CONV_A in m for m in logs) and any("新开会话" in m for m in logs)
    # 没有作用域（写梗概）：不续接、不记、照旧调用（也不传续接参数）
    calls = []
    prov2 = WebChatProvider(cfg, browser=runner, driver_factory=lambda h, t: None,
                            chat=lambda d, s, p, i, *, timeout, log, fresh: calls.append(p) or ANSWER)
    gateway_for("web_chatgpt", prov2).generate("episode.idea", "S", "U", Idea)
    assert len(calls) == 1


def test_web_text_turn_limit_starts_new_conversation(db):
    runner = make_runner()
    runner.conversations = ConversationBook(db, max_turns=2)
    seen = []

    def fake_ask(driver, spec, prompt, images, *, timeout, log, fresh, conversation_url=None, info=None,
                 continue_prefix=""):
        seen.append(conversation_url)
        info.update(url=CONV_A if len(seen) < 3 else CONV_B, continued=bool(conversation_url), note="")
        return ANSWER

    prov = WebChatProvider(ProviderCfg(id="web_chatgpt", kind="web", site="chatgpt"), browser=runner,
                           chat=fake_ask, driver_factory=lambda h, t: None)
    gw = gateway_for("web_chatgpt", prov)
    with use_scope(Scope("episode", "7")):
        for _ in range(3):
            gw.generate("script", "S", "U", Idea, key="script_structure")
    assert seen == [None, CONV_A, None]


# ---------------------------------------------------------------- 接口
def test_settings_flag_and_conversations_api(api):
    st = api.get("/api/settings").json()
    assert st["web_reuse_conversation"] is True
    r = api.put("/api/settings", json={"web_reuse_conversation": False})
    assert r.status_code == 200 and r.json()["web_reuse_conversation"] is False
    assert api.get("/api/browser/conversations",
                   params={"scope_kind": "prop", "scope_id": "1"}).json()["reuse_enabled"] is False
    # 只改别的设置时开关不变
    api.put("/api/settings", json={"text_model": None})
    assert api.get("/api/settings").json()["web_reuse_conversation"] is False
    assert api.get("/api/browser/conversations", params={"scope_kind": "nope", "scope_id": "1"}).status_code == 400


def test_new_conversation_goes_into_job_payload(api):
    api.post("/api/projects", json={"title": "demo"})
    ch = api.post("/api/projects/demo/characters", json={"name": "陈师傅", "visual_prompt": "LOOK"}).json()
    r = api.post(f"/api/characters/{ch['id']}/portraits/draw",
                 json={"count": 1, "engine": "gpt_image", "new_conversation": True})
    assert r.status_code == 200, r.text
    job = api.get(f"/api/jobs/{r.json()['job_ids'][0]}").json()
    assert job["payload"]["new_conversation"] is True
    r = api.post(f"/api/characters/{ch['id']}/portraits/draw", json={"count": 1, "engine": "gpt_image"})
    assert "new_conversation" not in api.get(f"/api/jobs/{r.json()['job_ids'][0]}").json()["payload"]


def test_run_options_new_conversation_only_first_stage():
    from backend.pipeline.runs import RunOptions
    from dataclasses import asdict, replace
    o = RunOptions(new_conversation=True)
    assert asdict(replace(o, new_conversation=False))["new_conversation"] is False
    assert RunOptions(**asdict(o)).new_conversation is True


def test_manager_sets_scope_for_job(db):
    from backend.queue.handlers import HandlerRegistry
    from backend.queue.manager import JobManager
    from backend.engines.browser.conversations import current_scope

    reg = HandlerRegistry()
    got = {}

    @reg.register("image.prop")
    def h(ctx, payload):
        got["scope"] = current_scope()
        return {}

    m = JobManager(db, reg)
    jid = m.submit("image.prop", {"prop_id": 5, "new_conversation": True}, project_id=None)
    m._execute(jid)
    assert got["scope"] == Scope("prop", "5", None, True)
    assert current_scope() is None


def test_plan_fallback():
    p = Plan("chatgpt", Scope("shot", "1"), url=CONV_A, turns=3)
    assert p.continuing
    p.fallback("扩展太旧")
    assert not p.continuing and p.reason == "扩展太旧" and p.turns == 0


def test_model_table_exists(db):
    with db.session() as s:
        s.add(BrowserConversation(site="chatgpt", scope_kind="shot", scope_id="1", url=CONV_A))


def test_run_route_puts_new_conversation_on_first_stage_only(api):
    from backend.domain.models import Episode, Shot

    sv = api.app.state.services
    api.post("/api/projects", json={"title": "demo"})
    ep_id = api.post("/api/projects/demo/episodes", json={"source_text": "x"}).json()["id"]
    with sv.db.session() as s:
        s.get(Episode, ep_id).project.image_engine = "gpt_image"
        s.add(Shot(episode_id=ep_id, order=0, code="S01", image_prompt="a garage"))
    r = api.post(f"/api/episodes/{ep_id}/run", json={"stage": "keyframes", "new_conversation": True})
    assert r.status_code == 200, r.text
    assert api.get(f"/api/jobs/{r.json()['job_ids'][0]}").json()["payload"]["new_conversation"] is True
    r = api.post(f"/api/episodes/{ep_id}/run", json={"stage": "keyframes", "force": True})
    assert "new_conversation" not in api.get(f"/api/jobs/{r.json()['job_ids'][0]}").json()["payload"]
