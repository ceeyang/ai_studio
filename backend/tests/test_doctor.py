import httpx

from backend.config import ComfyCfg, Config, EngineCfg, LLMCfg, ProviderCfg, ServerCfg
from backend.doctor import run_doctor
from backend.engines.comfyui.client import ComfyClient
from backend.engines.ffmpeg import FFmpegError
from backend.engines.registry import EngineRegistry

ONLY_ZIMAGE = {"h3_image": EngineCfg(enabled=False), "h3_video": EngineCfg(enabled=False),
               "wan22_i2v": EngineCfg(enabled=False)}
PROVIDERS = [ProviderCfg(id="lm", kind="openai", base_url="http://lm.test/v1"),
             ProviderCfg(id="claude", kind="claude"), ProviderCfg(id="m", kind="manual")]


class FakeFF:
    def __init__(self, text="ffmpeg version 8.1-full --enable-libass", fail=False):
        self.text, self.fail = text, fail

    def version(self):
        if self.fail:
            raise FFmpegError("找不到 ffmpeg")
        return self.text


def by_key(checks):
    return {(c.group, c.name): c for c in checks}


def make_cfg(tmp_path, url, models_dir=None):
    return Config(server=ServerCfg(data_dir=tmp_path / "data"),
                  comfyui=ComfyCfg(url=url, models_dir=models_dir),
                  engines=ONLY_ZIMAGE, llm=LLMCfg(providers=PROVIDERS))


def test_doctor_all_good(tmp_path, fake_comfy):
    models = tmp_path / "models"
    for sub, f in [("diffusion_models", "z_image_turbo_bf16.safetensors"),
                   ("text_encoders", "Qwen_3_4B.safetensors"), ("vae", "ae.safetensors")]:
        (models / sub).mkdir(parents=True, exist_ok=True)
        (models / sub / f).write_bytes(b"")
    cfg = make_cfg(tmp_path, fake_comfy.url, models)
    reg = EngineRegistry(cfg)
    fake_comfy.object_info = {ct: {} for ct in reg.get("zimage").tpl.class_types()}
    c = by_key(run_doctor(cfg, reg, ComfyClient(fake_comfy.url, backoff=0), FakeFF(),
                          http_get=lambda url: {"data": [{"id": "gemma"}]},
                          which=lambda n: "C:/bin/claude.exe"))
    assert c[("ComfyUI", "服务")].status == "ok" and "12.0" in c[("ComfyUI", "服务")].detail
    assert c[("引擎", "zimage 节点")].status == "ok"
    assert c[("引擎", "zimage 模型")].status == "ok"
    assert [c[("LLM", x)].status for x in ("lm", "claude", "m")] == ["ok", "ok", "ok"]
    assert c[("工具", "ffmpeg")].status == "ok"
    assert c[("系统", "磁盘")].status in ("ok", "warn") and c[("系统", "内存")].status in ("ok", "warn")


def test_doctor_reports_missing_things(tmp_path, fake_comfy):
    models = tmp_path / "models"
    models.mkdir()
    fake_comfy.object_info = {"KSampler": {}}
    cfg = make_cfg(tmp_path, fake_comfy.url, models)

    def down(url):
        raise httpx.ConnectError("refused")

    c = by_key(run_doctor(cfg, EngineRegistry(cfg), ComfyClient(fake_comfy.url, backoff=0),
                          FakeFF("ffmpeg version 8.1"), http_get=down, which=lambda n: None))
    assert c[("引擎", "zimage 节点")].status == "fail" and "UNETLoader" in c[("引擎", "zimage 节点")].detail
    assert c[("引擎", "zimage 模型")].status == "fail" and "ae.safetensors" in c[("引擎", "zimage 模型")].detail
    assert c[("LLM", "lm")].status == "warn" and c[("LLM", "claude")].status == "fail"
    assert c[("工具", "ffmpeg")].status == "warn"


def test_doctor_comfy_down(tmp_path):
    cfg = make_cfg(tmp_path, "http://127.0.0.1:9")
    checks = run_doctor(cfg, EngineRegistry(cfg), ComfyClient("http://127.0.0.1:9", backoff=0),
                        FakeFF(fail=True), http_get=lambda u: {"data": []}, which=lambda n: "x")
    c = by_key(checks)
    assert c[("ComfyUI", "服务")].status == "fail"
    assert not any(x.name.endswith("节点") for x in checks)
    assert c[("引擎", "模型目录")].status == "warn"
    assert c[("工具", "ffmpeg")].status == "fail"


def test_doctor_endpoint(api):
    rows = api.get("/api/doctor").json()
    assert any(r["group"] == "ComfyUI" and r["status"] == "fail" for r in rows)
    assert {"group", "name", "status", "detail"} <= set(rows[0])


class StatsOnlyComfy:
    """system_stats 正常、object_info 失败的 ComfyUI。"""

    def __init__(self):
        self.info_kw = None

    def system_stats(self, *, tries=None, timeout=None):
        return {"devices": [{"name": "GPU", "vram_free": 2**30, "vram_total": 2**31}]}

    def object_info(self, **kw):
        from backend.engines.comfyui.errors import ComfyError
        self.info_kw = kw
        raise ComfyError("超时")


def test_doctor_object_info_failure_is_its_own_row(tmp_path):
    cfg = make_cfg(tmp_path, "http://127.0.0.1:9")
    comfy = StatsOnlyComfy()
    checks = run_doctor(cfg, EngineRegistry(cfg), comfy, FakeFF(),
                        http_get=lambda u: {"data": []}, which=lambda n: "x")
    rows = [(x.group, x.name, x.status) for x in checks if x.group == "ComfyUI"]
    assert rows == [("ComfyUI", "服务", "ok"), ("ComfyUI", "节点信息", "fail")]
    assert comfy.info_kw == {"tries": 1, "timeout": 15}


def test_doctor_does_not_create_data_dir(tmp_path):
    cfg = make_cfg(tmp_path, "http://127.0.0.1:9")
    checks = run_doctor(cfg, EngineRegistry(cfg), None, FakeFF(),
                        http_get=lambda u: {"data": []}, which=lambda n: "x")
    assert not cfg.data_dir.exists()
    assert by_key(checks)[("系统", "磁盘")].status in ("ok", "warn")


def test_object_info_accepts_tries_and_timeout(fake_comfy):
    fake_comfy.object_info = {"KSampler": {}}
    assert "KSampler" in ComfyClient(fake_comfy.url, backoff=0).object_info(tries=1, timeout=15)
