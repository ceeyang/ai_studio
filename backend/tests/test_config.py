import textwrap

import pytest

from backend.config import ROOT, load_config


def write_cfg(tmp_path, text):
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent(text), encoding="utf-8")
    return p


def test_relative_paths_resolve_against_config_dir(tmp_path):
    cfg = load_config(write_cfg(tmp_path, """
        server: {data_dir: data}
        comfyui: {models_dir: models}
    """))
    assert cfg.data_dir == (tmp_path / "data").resolve()
    assert cfg.comfyui.models_dir == (tmp_path / "models").resolve()
    # 没写的路径用仓库内默认值
    assert cfg.workflows_dir == ROOT / "workflows"
    assert cfg.log_dir == ROOT / "logs"


def test_provider_routing(tmp_path):
    cfg = load_config(write_cfg(tmp_path, """
        llm:
          default: lmstudio
          providers:
            - {id: lmstudio, kind: openai, base_url: "http://127.0.0.1:1234/v1", local_gpu: true}
            - {id: claude, kind: claude}
          routes: {script: claude}
    """))
    assert cfg.llm.provider_for("script").id == "claude"
    assert cfg.llm.provider_for("storyboard").id == "lmstudio"
    assert cfg.llm.provider("lmstudio").local_gpu is True


def test_unknown_provider_raises(tmp_path):
    cfg = load_config(write_cfg(tmp_path, """
        llm:
          providers: [{id: claude, kind: claude}]
          routes: {script: nope}
    """))
    with pytest.raises(KeyError, match="nope"):
        cfg.llm.provider_for("script")
    with pytest.raises(KeyError, match="storyboard"):
        cfg.llm.provider_for("storyboard")


def test_api_key_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_STUDIO_KEY", "sk-test")
    cfg = load_config(write_cfg(tmp_path, """
        llm:
          providers: [{id: ds, kind: openai, base_url: "http://x/v1", api_key_env: TEST_STUDIO_KEY}]
    """))
    assert cfg.llm.provider("ds").api_key() == "sk-test"


def test_engine_defaults_when_missing(tmp_path):
    cfg = load_config(write_cfg(tmp_path, "engines: {zimage: {params: {steps: 8}}}\n"))
    assert cfg.engine("zimage").params == {"steps": 8}
    assert cfg.engine("not_configured").enabled is True


def test_example_config_loads():
    cfg = load_config(ROOT / "config.example.yaml")
    assert cfg.server.port == 8700
    assert cfg.server.host == "127.0.0.1"
    assert {"zimage", "h3_image", "h3_video", "wan22_i2v"} <= set(cfg.engines)
