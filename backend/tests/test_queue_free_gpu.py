"""POST /api/queue/free-gpu：手动释放 ComfyUI 显存。
有 GPU 任务在跑时拒绝（409），没有就调 ComfyUI /free 并报告释放前后的显存/内存快照。
"""
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.config import ComfyCfg, Config, LLMCfg, ProviderCfg, ServerCfg
from backend.domain.models import Job


def make_client(tmp_path, comfy_url):
    cfg = Config(server=ServerCfg(data_dir=tmp_path / "data"), log_dir=tmp_path / "logs",
                 comfyui=ComfyCfg(url=comfy_url, max_retries=1),
                 llm=LLMCfg(default="manual", providers=[ProviderCfg(id="manual", kind="manual")]),
                 frontend_dist=None)
    return TestClient(create_app(cfg, start_workers=False, configure_logging=False))


def test_free_gpu_calls_comfy_free_and_reports_before_after(tmp_path, fake_comfy):
    with make_client(tmp_path, fake_comfy.url) as c:
        c.app.state.manager.current_family = "h3"
        r = c.post("/api/queue/free-gpu")
        assert r.status_code == 200
        body = r.json()
        assert body["before"]["vram_free_gb"] == 12.0
        assert body["after"]["vram_free_gb"] == 12.0
        assert body["before"]["ram_free_gb"] == 32.0
        assert "free" in fake_comfy.calls
        assert c.app.state.manager.current_family is None


def test_free_gpu_refuses_while_gpu_job_running(tmp_path, fake_comfy):
    with make_client(tmp_path, fake_comfy.url) as c:
        with c.app.state.services.db.session() as s:
            s.add(Job(type="g", status="running", gpu=True))
        r = c.post("/api/queue/free-gpu")
        assert r.status_code == 409
        assert "有任务正在用显卡" in r.json()["detail"]
        assert "free" not in fake_comfy.calls


def test_free_gpu_allows_when_only_cpu_job_running(tmp_path, fake_comfy):
    with make_client(tmp_path, fake_comfy.url) as c:
        with c.app.state.services.db.session() as s:
            s.add(Job(type="cpu_job", status="running", gpu=False))
        r = c.post("/api/queue/free-gpu")
        assert r.status_code == 200
        assert "free" in fake_comfy.calls
