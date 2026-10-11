"""参考图反推外观（C）：LLM 网关看图、角色参考照片、character.from_photo 任务。"""
import asyncio
import base64
import json
import subprocess

import httpx
import pytest
from pydantic import BaseModel

from backend.config import LLMCfg, ProviderCfg
from backend.llm.base import LLMError, LLMResult
from backend.llm.gateway import LLMGateway
from backend.llm.providers import ClaudeProvider, OpenAIProvider
from backend.queue.errors import NeedsInput
from backend.tests.helpers import FakeCtx, FakeProvider, make_services

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32
LOOK = ("Chinese woman in her late twenties with an oval face, almond eyes and a small straight "
        "nose, fair skin, shoulder length straight black hair with blunt bangs, slim build, a "
        "cream cable knit sweater with a rolled collar over a white cotton shirt, a long camel "
        "wool coat with wide lapels, a pair of thin round gold rimmed glasses as the signature "
        "detail, natural minimal makeup, neat and composed appearance, simple everyday style "
        "suited to a young office worker in a big city")


class Out(BaseModel):
    n: int


class VisionProvider(FakeProvider):
    supports_images = True

    def complete(self, system, messages, *, json_mode=True, images=None):
        res = super().complete(system, messages, json_mode=json_mode)
        self.calls[-1]["images"] = list(images or [])
        return res


def gateway(prov, kind="openai", **pkw):
    cfg = LLMCfg(default="p", providers=[ProviderCfg(id="p", kind=kind, base_url="http://x/v1",
                                                     **pkw)])
    return LLMGateway(cfg, {"p": prov})


def png(tmp_path, name="ref.png"):
    f = tmp_path / name
    f.write_bytes(PNG)
    return f


# ---------------------------------------------------------------- 网关
def test_gateway_passes_images_to_vision_provider(tmp_path):
    prov = VisionProvider(['{"n": 1}'])
    f = png(tmp_path)
    assert gateway(prov).generate("t", "s", "u", Out, images=[f]).n == 1
    assert prov.calls[0]["images"] == [f]


def test_gateway_without_images_keeps_old_call_shape():
    prov = FakeProvider(['{"n": 1}'])     # 老 provider 的 complete 不认识 images
    assert gateway(prov).generate("t", "s", "u", Out).n == 1


def test_provider_without_vision_fails_with_settings_hint(tmp_path):
    with pytest.raises(LLMError) as ei:
        gateway(FakeProvider(['{"n": 1}'])).generate("t", "s", "u", Out, images=[png(tmp_path)],
                                                    step="character.from_photo")
    msg = str(ei.value)
    assert "看不了图片" in msg and "设置" in msg and "从参考图写外观" in msg and "手动粘贴" in msg
    with pytest.raises(LLMError, match="看不了图片"):
        gateway(VisionProvider(['{"n": 1}']), vision=False).generate(
            "t", "s", "u", Out, images=[png(tmp_path)], step="character.from_photo")


def test_manual_request_shows_image_paths(tmp_path):
    f = png(tmp_path)
    cfg = LLMCfg(default="m", providers=[ProviderCfg(id="m", kind="manual")])
    with pytest.raises(NeedsInput) as ei:
        LLMGateway(cfg, {}).generate("t", "s", "u", Out, key="k", images=[f],
                                     image_urls=["/media/demo/a.png"])
    assert ei.value.request["images"] == [str(f)]
    assert ei.value.request["image_urls"] == ["/media/demo/a.png"]


def test_openai_provider_sends_image_url_data_uri(tmp_path):
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.read())
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"n": 1}'}}]})

    p = OpenAIProvider(ProviderCfg(id="o", kind="openai", base_url="http://llm.test/v1"),
                       transport=httpx.MockTransport(handler))
    assert p.supports_images is True
    p.complete("S", [{"role": "user", "content": "看图"}], images=[png(tmp_path)])
    content = seen["body"]["messages"][1]["content"]
    assert content[0] == {"type": "text", "text": "看图"}
    url = content[1]["image_url"]["url"]
    assert content[1]["type"] == "image_url" and url.startswith("data:image/png;base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == PNG


def test_openai_rejecting_images_suggests_switching_model(tmp_path):
    p = OpenAIProvider(ProviderCfg(id="o", kind="openai", base_url="http://llm.test/v1"),
                       transport=httpx.MockTransport(
                           lambda r: httpx.Response(400, text="image input not supported")))
    cfg = LLMCfg(default="o", providers=[p.cfg])
    with pytest.raises(LLMError) as ei:
        LLMGateway(cfg, {"o": p}).generate("t", "s", "u", Out, images=[png(tmp_path)],
                                           step="character.from_photo")
    assert "看不了图片" in str(ei.value) and "image input not supported" in str(ei.value)


def test_unsupported_image_format(tmp_path):
    f = tmp_path / "a.bmp"
    f.write_bytes(b"BM")
    p = OpenAIProvider(ProviderCfg(id="o", kind="openai", base_url="http://llm.test/v1"),
                       transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    with pytest.raises(LLMError, match="不支持的图片格式"):
        p.complete("S", [{"role": "user", "content": "u"}], images=[f])


class StreamRun:
    def __init__(self, stdout):
        self.stdout = stdout

    def __call__(self, cmd, **kw):
        self.cmd, self.kw = cmd, kw
        return subprocess.CompletedProcess(cmd, 0, self.stdout, "")


def test_claude_cli_sends_image_block_via_stream_json(tmp_path):
    out = "\n".join([json.dumps({"type": "system", "subtype": "init"}),
                     json.dumps({"type": "assistant", "message": {}}),
                     json.dumps({"type": "result", "subtype": "success", "result": '{"n": 1}'})])
    run = StreamRun(out)
    p = ClaudeProvider(ProviderCfg(id="c", kind="claude", use_sdk=False), run=run,
                       which=lambda n: "C:/fake/claude.exe")
    assert p.supports_images is True
    res = p.complete("S", [{"role": "user", "content": "看图"}], images=[png(tmp_path)])
    assert res.text == '{"n": 1}'
    assert "--input-format" in run.cmd and run.cmd[run.cmd.index("--input-format") + 1] == "stream-json"
    assert run.cmd[run.cmd.index("--output-format") + 1] == "stream-json"
    msg = json.loads(run.kw["input"].strip())
    assert msg["type"] == "user"
    blocks = msg["message"]["content"]
    assert blocks[0]["type"] == "image" and blocks[0]["source"]["media_type"] == "image/png"
    assert base64.b64decode(blocks[0]["source"]["data"]) == PNG
    assert blocks[1] == {"type": "text", "text": "看图"}


def test_claude_cli_stream_error(tmp_path):
    run = StreamRun(json.dumps({"type": "result", "is_error": True, "result": "不支持"}))
    p = ClaudeProvider(ProviderCfg(id="c", kind="claude", use_sdk=False), run=run,
                       which=lambda n: "C:/fake/claude.exe")
    with pytest.raises(LLMError, match="报错"):
        p.complete("S", [{"role": "user", "content": "u"}], images=[png(tmp_path)])


def test_claude_sdk_streams_image_message(tmp_path):
    got = {}

    class Sdk(ClaudeProvider):
        async def _sdk(self, system, prompt):
            got["messages"] = [m async for m in prompt]
            return '{"n": 1}'

    p = Sdk(ProviderCfg(id="c", kind="claude", use_sdk=True), run=None, which=lambda n: None)
    assert p.complete("S", [{"role": "user", "content": "看图"}],
                      images=[png(tmp_path)]).text == '{"n": 1}'
    (msg,) = got["messages"]
    assert msg["message"]["content"][0]["type"] == "image"
    assert msg["message"]["content"][-1]["text"] == "看图"


# ---------------------------------------------------------------- API 与任务
def make_char(api, name="林夏"):
    api.post("/api/projects", json={"title": "demo"})
    r = api.post("/api/projects/demo/characters",
                 json={"name": name, "description": "28 岁主播", "visual_prompt": "OLD LOOK"})
    return r.json()


def test_upload_appearance_ref_is_not_a_portrait(api):
    ch = make_char(api)
    assert ch["appearance_ref"] is None
    r = api.post(f"/api/characters/{ch['id']}/appearance-ref",
                 files={"file": ("ref.png", PNG, "image/png")})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["appearance_ref"]["url"].startswith("/media/demo/media/appearance_ref/")
    assert out["appearance_ref"]["visual_prompt"] is None
    assert api.get(out["appearance_ref"]["url"]).content == PNG
    # 不是定妆照、不当参考图
    assert out["portrait_url"] is None and out["portraits"] == []
    r = api.post(f"/api/characters/{ch['id']}/appearance-ref",
                 files={"file": ("ref.gif", b"GIF", "image/gif")})
    assert r.status_code == 400


def test_from_photo_requires_ref_then_waits_for_manual_input_with_image(api):
    ch = make_char(api)
    r = api.post(f"/api/characters/{ch['id']}/appearance-from-photo")
    assert r.status_code == 400 and "参考照片" in r.json()["detail"]
    api.post(f"/api/characters/{ch['id']}/appearance-ref",
             files={"file": ("ref.png", PNG, "image/png")})
    r = api.post(f"/api/characters/{ch['id']}/appearance-from-photo")
    assert r.status_code == 200, r.text
    jid = r.json()["job_ids"][0]
    m = api.app.state.manager
    m._execute(m._claim(gpu=False))
    job = api.get(f"/api/jobs/{jid}").json()
    assert job["status"] == "waiting_input"
    req = job["result"]["input_request"]
    assert req["key"] == f"character.from_photo:{ch['id']}"
    assert req["images"][0].endswith(".png") and req["image_urls"][0].startswith("/media/demo/")
    assert "只写外观" in req["system"] and "明星" in req["system"]
    # 进行中的任务挂在角色上（抽屉里显示「正在看图」）
    assert [j["type"] for j in api.get("/api/projects/demo/characters").json()[0]["active_jobs"]] \
        == ["character.from_photo"]
    # 人工把结果贴回来：外观记在参考照片上，等用户确认，不直接改角色
    r = api.post(f"/api/jobs/{jid}/input",
                 json={"text": json.dumps({"visual_prompt": LOOK, "core_features": "齐刘海、金丝圆眼镜"})})
    assert r.status_code == 200, r.text
    m._execute(m._claim(gpu=False))
    assert api.get(f"/api/jobs/{jid}").json()["status"] == "done"
    c = api.get("/api/projects/demo/characters").json()[0]
    assert c["visual_prompt"] == "OLD LOOK"
    assert c["appearance_ref"]["visual_prompt"] == LOOK
    assert c["appearance_ref"]["core_features"] == "齐刘海、金丝圆眼镜"
    # 放弃
    r = api.delete(f"/api/characters/{ch['id']}/appearance-from-photo")
    assert r.status_code == 200 and r.json()["appearance_ref"]["visual_prompt"] is None
    assert r.json()["appearance_ref"]["url"]


def test_from_photo_handler_with_vision_provider_retries_chinese(tmp_path, db):
    from backend.domain.characters import set_appearance_ref
    from backend.domain.models import Character, Project
    from backend.pipeline.characters import run_from_photo

    bad = {"visual_prompt": LOOK.replace("glasses", "眼镜"), "core_features": "眼镜"}
    good = {"visual_prompt": LOOK, "core_features": "金丝圆眼镜"}
    prov = VisionProvider([bad, good])
    cfg = LLMCfg(default="v", providers=[ProviderCfg(id="v", kind="openai", base_url="http://x")])
    sv = make_services(tmp_path, db, llm=LLMGateway(cfg, {"v": prov}))
    with db.session() as s:
        p = Project(slug="demo", title="t")
        s.add(p)
        s.flush()
        ch = Character(project_id=p.id, name="林夏", visual_prompt="OLD")
        s.add(ch)
        s.flush()
        set_appearance_ref(s, sv.media, ch, PNG, "ref.png")
        cid = ch.id
    ctx = FakeCtx(sv, job_id=7)
    out = run_from_photo(ctx, {"character_id": cid})
    assert out["visual_prompt"] == LOOK
    assert prov.calls[0]["images"][0].read_bytes() == PNG
    assert "中文" in prov.calls[1]["messages"][-1]["content"]
    assert prov.calls[0]["system"].startswith("你是 AI 短剧的角色造型师")
    with db.session() as s:
        from backend.domain.characters import appearance_ref_of

        ref = appearance_ref_of(s, cid)
        assert ref.params["look"] == {"visual_prompt": LOOK, "core_features": "金丝圆眼镜",
                                      "job_id": 7}
        assert s.get(Character, cid).visual_prompt == "OLD"


def test_from_photo_handler_fails_clearly_without_vision(tmp_path, db):
    from backend.domain.characters import set_appearance_ref
    from backend.domain.models import Character, Project
    from backend.pipeline.characters import run_from_photo

    cfg = LLMCfg(default="t", providers=[ProviderCfg(id="t", kind="openai", base_url="http://x")])
    sv = make_services(tmp_path, db, llm=LLMGateway(cfg, {"t": FakeProvider([])}))
    with db.session() as s:
        p = Project(slug="demo", title="t")
        s.add(p)
        s.flush()
        ch = Character(project_id=p.id, name="林夏")
        s.add(ch)
        s.flush()
        set_appearance_ref(s, sv.media, ch, PNG, "ref.png")
        cid = ch.id
    with pytest.raises(LLMError, match="从参考图写外观"):
        run_from_photo(FakeCtx(sv), {"character_id": cid})


def test_photo_look_problems():
    from backend.pipeline.characters import photo_look_problems

    assert photo_look_problems(LOOK) == []
    assert "中文" in photo_look_problems("a man 老周")[0]
    assert "姿势" in photo_look_problems("a man sitting on a stool")[0]
