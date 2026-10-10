"""测试公共 fixture。"""
import pytest

from backend.domain.db import Database


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "studio.db")
    d.create_all()
    yield d
    d.engine.dispose()


from backend.tests.fakes.fake_comfy import FakeComfy


@pytest.fixture
def fake_comfy():
    fc = FakeComfy()
    fc.start()
    yield fc
    fc.stop()


from backend.config import ComfyCfg, Config, LLMCfg, ProviderCfg, ServerCfg


@pytest.fixture
def api(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from backend.app.main import create_app
    from backend.engines.comfyui import health

    # 测试配置里的 ComfyUI 地址连不上；默认当它在线，测「连不上」的用例自己再 monkeypatch
    monkeypatch.setattr(health, "check_comfy",
                        lambda client: health.ComfyHealth(True, "已连上（测试）"))

    cfg = Config(server=ServerCfg(data_dir=tmp_path / "data"), log_dir=tmp_path / "logs",
                 comfyui=ComfyCfg(url="http://127.0.0.1:9", max_retries=1),
                 llm=LLMCfg(default="manual", providers=[ProviderCfg(id="manual", kind="manual")]),
                 frontend_dist=None)
    app = create_app(cfg, start_workers=False, configure_logging=False)
    with TestClient(app) as client:
        yield client
