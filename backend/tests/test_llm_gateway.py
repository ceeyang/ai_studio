import json
import subprocess
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
from pydantic import BaseModel

from backend.config import LLMCfg, ProviderCfg
from backend.llm.base import LLMError
from backend.llm.gateway import LLMGateway
from backend.llm.providers import ClaudeProvider, OpenAIProvider, build_providers
from backend.queue.errors import NeedsInput
from backend.tests.helpers import FakeProvider


class Out(BaseModel):
    n: int


def gw(texts, kind="openai", **pkw):
    prov = FakeProvider(texts)
    cfg = LLMCfg(default="p", providers=[ProviderCfg(id="p", kind=kind, base_url="http://x/v1", **pkw)])
    return LLMGateway(cfg, {"p": prov}), prov


def test_valid_first_try():
    g, p = gw(['{"n": 1}'])
    assert g.generate("t", "sys", "u", Out).n == 1
    assert len(p.calls) == 1 and p.calls[0]["system"] == "sys"
    assert p.calls[0]["messages"] == [{"role": "user", "content": "u"}]


def test_retry_with_error_feedback():
    g, p = gw(["不是 JSON", '{"n": 2}'])
    assert g.generate("t", "s", "u", Out).n == 2
    msgs = p.calls[1]["messages"]
    assert msgs[1] == {"role": "assistant", "content": "不是 JSON"}
    assert "不符合要求" in msgs[2]["content"]


def test_check_failure_triggers_retry():
    g, p = gw(['{"n": 1}', '{"n": 3}'])

    def check(o):
        if o.n != 3:
            raise ValueError("需要 n=3")

    assert g.generate("t", "s", "u", Out, check=check).n == 3
    assert "需要 n=3" in p.calls[1]["messages"][2]["content"]


def test_gives_up_after_three_and_keeps_raw():
    g, _ = gw(["坏1", "坏2", "坏3"])
    with pytest.raises(LLMError) as ei:
        g.generate("t", "s", "u", Out)
    assert "3 次" in str(ei.value) and "坏3" in str(ei.value)


def test_manual_without_result_needs_input():
    g, _ = gw([], kind="manual")
    with pytest.raises(NeedsInput) as ei:
        g.generate("t", "sys", "user", Out, key="k1")
    r = ei.value.request
    assert r["key"] == "k1" and r["system"] == "sys" and r["user"] == "user"
    assert "properties" in r["schema"]


def test_manual_with_result_and_invalid_result():
    g, _ = gw([], kind="manual")
    assert g.generate("t", "s", "u", Out, manual={"k1": '{"n": 5}'}, key="k1").n == 5
    with pytest.raises(NeedsInput) as ei:
        g.generate("t", "s", "u", Out, manual={"k1": "乱写"}, key="k1")
    assert ei.value.request["error"]


def test_local_gpu_uses_guard_and_unloads():
    events = []

    @contextmanager
    def guard():
        events.append("enter")
        yield
        events.append("exit")

    def fake_run(cmd, **kw):
        events.append(("run", tuple(cmd)))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    cfg = LLMCfg(default="p", providers=[ProviderCfg(id="p", kind="openai", base_url="http://x/v1",
                                                     local_gpu=True,
                                                     unload_cmd=["lms", "unload", "--all"])])
    g = LLMGateway(cfg, {"p": FakeProvider(['{"n": 1}'])}, gpu_guard=guard, run=fake_run)
    g.generate("t", "s", "u", Out)
    assert events == ["enter", ("run", ("lms", "unload", "--all")), "exit"]


def test_openai_provider_request_shape():
    seen = {}

    def handler(req):
        seen["url"] = str(req.url)
        seen["body"] = json.loads(req.read())
        seen["auth"] = req.headers.get("authorization")
        return httpx.Response(200, json={"model": "m1", "usage": {"total_tokens": 9},
                                         "choices": [{"message": {"content": '{"n": 1}'}}]})

    cfg = ProviderCfg(id="ds", kind="openai", base_url="http://llm.test/v1", model="m1")
    res = OpenAIProvider(cfg, transport=httpx.MockTransport(handler)).complete(
        "SYS", [{"role": "user", "content": "U"}])
    assert seen["url"] == "http://llm.test/v1/chat/completions"
    assert seen["body"]["messages"][0] == {"role": "system", "content": "SYS"}
    assert seen["body"]["model"] == "m1" and seen["auth"] is None
    assert res.text == '{"n": 1}' and res.usage == {"total_tokens": 9}


def test_openai_provider_http_error():
    cfg = ProviderCfg(id="ds", kind="openai", base_url="http://llm.test/v1")
    p = OpenAIProvider(cfg, transport=httpx.MockTransport(lambda r: httpx.Response(500, text="炸了")))
    with pytest.raises(LLMError, match="500"):
        p.complete("s", [{"role": "user", "content": "u"}])


class FakeRun:
    def __init__(self, stdout, returncode=0, stderr=""):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr

    def __call__(self, cmd, **kw):
        self.cmd, self.kw = cmd, kw
        self.system = Path(cmd[cmd.index("--system-prompt-file") + 1]).read_text(encoding="utf-8")
        return subprocess.CompletedProcess(cmd, self.returncode, self.stdout, self.stderr)


def claude(run, use_sdk=False):
    cfg = ProviderCfg(id="claude", kind="claude", use_sdk=use_sdk)
    return ClaudeProvider(cfg, run=run, which=lambda name: "C:/fake/claude.exe")


def test_claude_cli_path():
    run = FakeRun(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                              "result": '{"n": 1}', "usage": {"output_tokens": 3}}))
    res = claude(run).complete("系统提示", [{"role": "user", "content": "用户消息"}])
    assert res.text == '{"n": 1}'
    assert run.cmd[:4] == ["C:/fake/claude.exe", "-p", "--output-format", "json"]
    assert run.kw["input"] == "用户消息" and run.system == "系统提示"


def test_claude_cli_errors():
    with pytest.raises(LLMError, match="报错"):
        claude(FakeRun(json.dumps({"type": "result", "is_error": True, "result": "额度用完"}))).complete(
            "s", [{"role": "user", "content": "u"}])
    with pytest.raises(LLMError, match="不是 JSON"):
        claude(FakeRun("oops")).complete("s", [{"role": "user", "content": "u"}])
    p = ClaudeProvider(ProviderCfg(id="c", kind="claude", use_sdk=False), which=lambda n: None)
    with pytest.raises(LLMError, match="找不到 claude"):
        p.complete("s", [{"role": "user", "content": "u"}])


def test_claude_sdk_failure_falls_back_to_cli():
    class Broken(ClaudeProvider):
        async def _sdk(self, system, prompt):
            raise RuntimeError("sdk 坏了")

    run = FakeRun(json.dumps({"type": "result", "subtype": "success", "result": "ok"}))
    p = Broken(ProviderCfg(id="c", kind="claude", use_sdk=True), run=run,
               which=lambda n: "C:/fake/claude.exe")
    assert p.complete("s", [{"role": "user", "content": "u"}]).text == "ok"


def test_build_providers_skips_manual():
    cfg = LLMCfg(providers=[ProviderCfg(id="a", kind="openai", base_url="http://x/v1"),
                            ProviderCfg(id="b", kind="claude"), ProviderCfg(id="m", kind="manual")])
    assert sorted(build_providers(cfg)) == ["a", "b"]


def test_check_learns_last_from_real_attempt_index_even_after_schema_failures():
    """check 可以声明 last：按网关真实的调用次数算。前两次连 schema 都不合格（check 没被调用），
    第三次才是最后一次机会。"""
    seen = []

    def check(o, *, last):
        seen.append(last)

    g, _ = gw(["坏1", "坏2", '{"n": 1}'])
    assert g.generate("t", "s", "u", Out, check=check).n == 1
    assert seen == [True]
    seen.clear()
    g, _ = gw(['{"n": 1}'])
    g.generate("t", "s", "u", Out, check=check)
    assert seen == [False]


def test_manual_result_is_checked_as_last_chance():
    seen = []
    g, _ = gw([], kind="manual")
    g.generate("t", "s", "u", Out, check=lambda o, last: seen.append(last),
               manual={"k1": '{"n": 5}'}, key="k1")
    assert seen == [True]
    # 不声明 last 的老 check 照常工作
    assert g.generate("t", "s", "u", Out, check=lambda o: None, manual={"k1": '{"n": 5}'},
                      key="k1").n == 5
