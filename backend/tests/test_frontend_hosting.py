"""生产态托管前端：/ 返回页面，前端路由回落到 index.html，不遮挡 /api 和 /media。"""
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.config import ComfyCfg, Config, LLMCfg, ProviderCfg, ServerCfg, load_config


def make_client(tmp_path, dist):
    cfg = Config(server=ServerCfg(data_dir=tmp_path / "data"), log_dir=tmp_path / "logs",
                 comfyui=ComfyCfg(url="http://127.0.0.1:9", max_retries=1),
                 llm=LLMCfg(default="manual", providers=[ProviderCfg(id="manual", kind="manual")]),
                 frontend_dist=dist)
    return TestClient(create_app(cfg, start_workers=False, configure_logging=False))


def build_dist(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>AI Studio</title>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")
    return dist


def test_serves_index_assets_and_spa_routes(tmp_path):
    with make_client(tmp_path, build_dist(tmp_path)) as c:
        r = c.get("/")
        assert r.status_code == 200 and "AI Studio" in r.text
        assert r.headers["content-type"].startswith("text/html")
        assert r.headers["cache-control"] == "no-cache"
        assert c.get("/assets/app.js").text == "console.log(1)"
        deep = c.get("/projects/提词员/episodes/1/director")
        assert deep.status_code == 200 and "AI Studio" in deep.text
        assert c.get("/jobs?status=failed").status_code == 200


def test_api_and_media_are_not_shadowed(tmp_path):
    with make_client(tmp_path, build_dist(tmp_path)) as c:
        assert c.get("/api/health").json()["ok"] is True
        r = c.get("/api/nope")
        assert r.status_code == 404 and r.headers["content-type"].startswith("application/json")
        r = c.get("/media/demo/media/x.png")
        assert r.status_code == 404 and "AI Studio" not in r.text
        assert c.get("/media").status_code == 404
        assert c.get("/assets/missing.js").status_code == 404
        assert c.get("/favicon.ico").status_code == 404


def test_rejects_paths_outside_dist(tmp_path):
    with make_client(tmp_path, build_dist(tmp_path)) as c:
        for url in ["/..%2Fsecret.txt", "/assets/..%2F..%2Fsecret.txt",
                    "/%5C%5Chost%5Cshare%5Cx", "/C:%2Fsecret.txt"]:
            r = c.get(url)
            assert r.status_code == 404, (url, r.status_code)
            assert "secret" not in r.text, url


def test_no_dist_means_no_frontend(tmp_path):
    with make_client(tmp_path, tmp_path / "missing") as c:
        assert c.get("/").status_code == 404
        assert c.get("/api/health").status_code == 200
    with make_client(tmp_path, None) as c:
        assert c.get("/").status_code == 404


def test_frontend_dist_is_relative_to_config_file(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("frontend_dist: web/dist\n", encoding="utf-8")
    assert load_config(p).frontend_dist == (tmp_path / "web" / "dist").resolve()
