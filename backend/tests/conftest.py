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
def api(tmp_path):
    from fastapi.testclient import TestClient

    from backend.app.main import create_app

    cfg = Config(server=ServerCfg(data_dir=tmp_path / "data"), log_dir=tmp_path / "logs",
                 comfyui=ComfyCfg(url="http://127.0.0.1:9", max_retries=1),
                 llm=LLMCfg(default="manual", providers=[ProviderCfg(id="manual", kind="manual")]))
    app = create_app(cfg, start_workers=False, configure_logging=False)
    with TestClient(app) as client:
        yield client
