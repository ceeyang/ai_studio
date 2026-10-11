"""风格预设（A）：项目字段与迁移、API、进剧本 / 分镜提示词、不进指纹。"""
import sqlite3
import typing

import pytest
from alembic import command

from backend.domain.db import Database
from backend.domain.style_presets import PRESETS, get_preset, prompt_vars, recommended
from backend.llm.prompts import PromptLibrary
from backend.services import BUILTIN_DIR


def lib():
    return PromptLibrary(BUILTIN_DIR, BUILTIN_DIR / "none")


def test_six_presets_match_api_literal():
    from backend.app.schemas import StylePresetKey

    assert list(PRESETS) == ["live_action", "cg_realistic", "anime", "guofeng", "western_comic",
                             "commercial"]
    assert set(typing.get_args(StylePresetKey)) == set(PRESETS)
    assert [p.label for p in PRESETS.values()] == ["真人写实", "3D 写实", "日系动漫", "国风漫画",
                                                    "美式漫画", "商业广告"]
    assert get_preset(None).key == "live_action" and get_preset("nope").key == "live_action"
    assert PRESETS["commercial"].storyboard_guidance and not PRESETS["anime"].storyboard_guidance


def test_recommended_engine_picks_first_available():
    assert recommended(["zimage", "krea2"], ("krea2", "zimage")) == "krea2"
    assert recommended(["zimage"], ("gpt_image", "zimage")) == "zimage"
    assert recommended(["h3_image"], ("gpt_image",)) is None


def test_migration_defaults_existing_projects_to_live_action(tmp_path):
    from backend.tests.test_migrations import _cfg

    d = Database(tmp_path / "m.db")
    cfg = _cfg(d)
    command.upgrade(cfg, "f2b6d8e1a4c7")
    c = sqlite3.connect(d.path)
    c.execute("INSERT INTO projects (id, slug, title, aspect, image_engine, video_engine, seed, "
              "negative, created_at, archived) VALUES (1,'p','p','9:16','z','w',1,'','2026-01-01',0)")
    c.commit()
    c.close()
    command.upgrade(cfg, "head")
    d.engine.dispose()
    c = sqlite3.connect(d.path)
    try:
        assert c.execute("SELECT style_preset FROM projects").fetchone()[0] == "live_action"
    finally:
        c.close()


def test_api_lists_presets_with_recommended_engines(api):
    rows = api.get("/api/style-presets").json()
    assert [r["key"] for r in rows] == list(PRESETS)
    live = rows[0]
    assert live["label"] == "真人写实" and live["description"]
    assert live["image_engine"] in api.app.state.services.engines.names("image")
    assert live["video_engine"] == "h3_video"


def test_create_project_with_preset_fills_negative(api):
    r = api.post("/api/projects", json={"title": "动漫", "style_preset": "anime"})
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["style_preset"] == "anime" and p["negative"] == PRESETS["anime"].negative
    # 默认真人写实
    p = api.post("/api/projects", json={"title": "默认"}).json()
    assert p["style_preset"] == "live_action" and p["negative"] == PRESETS["live_action"].negative
    # 自己给了负面词就用自己的
    p = api.post("/api/projects", json={"title": "自定", "negative": "blurry"}).json()
    assert p["negative"] == "blurry"
    assert api.post("/api/projects", json={"title": "x", "style_preset": "oil"}).status_code == 422


def test_patch_preset_swaps_default_negative_only(api):
    api.post("/api/projects", json={"title": "demo"})
    r = api.patch("/api/projects/demo", json={"style_preset": "guofeng"})
    assert r.status_code == 200
    assert r.json()["style_preset"] == "guofeng"
    assert r.json()["negative"] == PRESETS["guofeng"].negative
    api.patch("/api/projects/demo", json={"negative": "my own"})
    r = api.patch("/api/projects/demo", json={"style_preset": "commercial"})
    assert r.json()["style_preset"] == "commercial" and r.json()["negative"] == "my own"


def test_visual_prompt_carries_preset_anchor_direction():
    system, _ = lib().render("script_visual", project_title="t", characters=[], locations=[],
                             excerpt="x", **prompt_vars("anime"))
    assert "日系动漫" in system and "cel-shaded" in system
    # 只写成像风格的限制还在
    assert "只写全剧通用的成像风格" in system and "上一条「只写成像风格」的限制不变" in system
    assert PRESETS["anime"].negative in system
    # 没传预设（项目目录里的老覆盖模板 / 旧调用）时不出现预设段落
    plain, _ = lib().render("script_visual", project_title="t", characters=[], locations=[],
                            excerpt="x")
    assert "风格预设" not in plain


@pytest.mark.parametrize("key", list(PRESETS))
def test_preset_guidance_has_no_scene_words(key):
    """预设的画风方向会被模型抄进风格锚：里面的英文不能有场景 / 天气 / 光源 / 道具词。"""
    from backend.pipeline.prompting import _SCENE_TERMS

    p = PRESETS[key]
    assert not _SCENE_TERMS.search(p.anchor_guidance), _SCENE_TERMS.findall(p.anchor_guidance)


def test_storyboard_prompt_commercial_guidance():
    kw = dict(anchor="a", characters=[], location=None, text="t", count=3, shot_seconds=5,
              max_seconds=10, prev_action="", block_seconds=15, previous_shots=[], known_props=[],
              aspect="9:16")
    system, _ = lib().render("storyboard", **kw, **prompt_vars("commercial"))
    assert "商业广告" in system and "product hero shot" in system and "行动号召" in system
    system, _ = lib().render("storyboard", **kw, **prompt_vars("anime"))
    assert "日系动漫风格" in system and "仿真人风格" not in system and "行动号召" not in system
    system, _ = lib().render("storyboard", **kw)
    assert "仿真人风格" in system


def test_structure_prompt_commercial_guidance():
    kw = dict(project_title="t", target_seconds=30, source_text="x", shot_count=6, shot_seconds=5,
              aspect="9:16")
    system, _ = lib().render("script_structure", **kw, **prompt_vars("commercial"))
    assert "商业广告" in system and "行动号召" in system
    system, _ = lib().render("script_structure", **kw, **prompt_vars("live_action"))
    assert "行动号召" not in system


def test_preset_change_does_not_change_fingerprints(tmp_path, db):
    """换预设不进指纹：下一次不强制的运行不会重写剧本和分镜。"""
    from backend.domain.models import Project
    from backend.pipeline.script import run_script
    from backend.tests.helpers import FakeCtx, make_gateway, make_services, seed_episode
    from backend.tests.test_script import STRUCTURE, VISUAL

    gw, prov = make_gateway([STRUCTURE, VISUAL])
    sv = make_services(tmp_path, db, llm=gw)
    _, ep_id = seed_episode(db)
    ctx = FakeCtx(sv)
    run_script(ctx, {"episode_id": ep_id})
    assert "真人写实" in prov.calls[1]["system"]
    with db.session() as s:
        s.query(Project).one().style_preset = "anime"
    assert run_script(ctx, {"episode_id": ep_id}) == {"structure": "skipped", "visual": "skipped"}


def test_storyboard_run_uses_project_preset(tmp_path, db):
    from backend.domain.models import Project
    from backend.pipeline.script import run_script
    from backend.pipeline.storyboard import build_storyboard_plan, storyboard_prompt_fp
    from backend.tests.helpers import FakeCtx, make_gateway, make_services, seed_episode
    from backend.tests.test_script import STRUCTURE, VISUAL

    gw, _ = make_gateway([STRUCTURE, VISUAL])
    sv = make_services(tmp_path, db, llm=gw)
    _, ep_id = seed_episode(db)
    run_script(FakeCtx(sv), {"episode_id": ep_id})
    with db.session() as s:
        s.query(Project).one().style_preset = "commercial"
    with db.session() as s:
        plan = build_storyboard_plan(s, sv, ep_id)
        blk = plan.blocks[0]
        system, _, fp = storyboard_prompt_fp(plan.prompts, plan.anchor, plan.char_table,
                                             blk["location"], blk["text"], blk["count"],
                                             plan.shot_seconds, plan.max_seconds, "",
                                             preset=plan.preset)
        _, _, fp_plain = storyboard_prompt_fp(plan.prompts, plan.anchor, plan.char_table,
                                              blk["location"], blk["text"], blk["count"],
                                              plan.shot_seconds, plan.max_seconds, "")
    assert plan.preset == "commercial" and "product hero shot" in system
    assert fp == fp_plain
