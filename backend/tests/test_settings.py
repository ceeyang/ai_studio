"""设置页「默认模型」：存取、生效顺序、LLM 网关调用时现读、API、新建项目默认值、迁移。"""
import sqlite3

from pydantic import BaseModel

from backend.config import LLMCfg, ProviderCfg
from backend.domain.db import Database
from backend.domain.models import Character
from backend.domain.settings import (SettingsStore, default_image_engine, default_video_engine,
                                     portrait_engine, sheet_engine)
from backend.llm.gateway import LLMGateway
from backend.tests.helpers import FakeCtx, FakeProvider, FakeRunner, make_services, seed_episode


class Out(BaseModel):
    n: int


# ---------------------------------------------------------------- 存取
def test_store_roundtrip_and_null_means_follow_default(db):
    st = SettingsStore(db)
    assert st.load().text_model is None and st.load().steps == {}
    st.save({"text_model": "claude", "image_engine": "krea2", "step:script": "lm",
             "step:portrait": "gpt_image"})
    got = st.load()
    assert (got.text_model, got.image_engine, got.video_engine) == ("claude", "krea2", None)
    assert got.steps == {"script": "lm", "portrait": "gpt_image"}
    st.save({"text_model": None, "step:script": ""})
    got = st.load()
    assert got.text_model is None and got.steps == {"portrait": "gpt_image"}
    st.save({"step:portrait": "zimage"})
    assert st.load().steps == {"portrait": "zimage"}


def test_llm_provider_order(db):
    st = SettingsStore(db)
    assert st.llm_provider("script") is None                 # 都没设：交给 config.yaml
    st.save({"text_model": "g"})
    assert st.llm_provider("script") == "g"                  # 全局默认
    st.save({"step:script": "s"})
    assert st.llm_provider("script") == "s" and st.llm_provider("storyboard") == "g"


# ---------------------------------------------------------------- 生效顺序（图片/视频）
def test_new_project_engine_defaults(tmp_path, db):
    """「新项目的图片/视频来源」只决定新建项目填什么；首帧/视频没有按步骤设置。"""
    sv = make_services(tmp_path, db)
    assert default_image_engine(sv) == "zimage" and default_video_engine(sv) == "h3_video"
    sv.settings.save({"image_engine": "krea2", "video_engine": "wan22_i2v"})
    assert default_image_engine(sv) == "krea2" and default_video_engine(sv) == "wan22_i2v"
    # 旧版本存过的首帧/视频步骤设置不再生效
    sv.settings.save({"step:keyframes": "h3_image", "step:videos": "h3_video"})
    assert sv.settings.load().steps == {}
    assert default_image_engine(sv) == "krea2" and default_video_engine(sv) == "wan22_i2v"
    # 设置里写了已经停用的引擎：跳过
    sv.settings.save({"image_engine": "nope"})
    assert default_image_engine(sv) == "zimage"


def test_store_save_same_new_key_concurrently(db):
    """两个请求同时写同一个新 key 不撞主键（INSERT … ON CONFLICT DO UPDATE）。"""
    import threading

    st = SettingsStore(db)
    errors = []

    def write(v):
        try:
            for _ in range(20):
                st.save({"text_model": v})
                st.save({"text_model": None})
                st.save({"text_model": v})
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    ts = [threading.Thread(target=write, args=(x,)) for x in ("a", "b", "c", "d")]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert errors == []
    assert st.load().text_model in {"a", "b", "c", "d"}


def test_portrait_and_sheet_resolution(tmp_path, db):
    sv = make_services(tmp_path, db)
    assert portrait_engine(sv, "zimage") == "zimage"                    # 跟项目
    assert sheet_engine(sv, "zimage") == "h3_image"                     # 三视图默认 H3
    sv.settings.save({"step:portrait": "krea2", "step:turnaround": "gpt_image"})
    assert portrait_engine(sv, "zimage") == "krea2"
    assert sheet_engine(sv, "zimage") == "gpt_image"
    assert portrait_engine(sv, "zimage", explicit="h3_image") == "h3_image"   # 明确指定最优先
    assert sheet_engine(sv, "zimage", explicit="zimage") == "zimage"


def test_portrait_job_uses_step_setting(tmp_path, db):
    from backend.pipeline.characters import run_portrait

    runner = FakeRunner()
    sv = make_services(tmp_path, db, comfy=runner)
    pid, _ = seed_episode(db, image_engine="zimage")
    with db.session() as s:
        c = Character(project_id=pid, name="陈", visual_prompt="short hair")
        s.add(c)
        s.flush()
        cid = c.id
    sv.settings.save({"step:portrait": "krea2"})
    run_portrait(FakeCtx(sv), {"character_id": cid})
    assert runner.calls[-1].engine == "krea2"
    run_portrait(FakeCtx(sv, job_id=2), {"character_id": cid, "engine": "zimage"})
    assert runner.calls[-1].engine == "zimage"


# ---------------------------------------------------------------- LLM 网关
def _gw(db, route=None):
    a, b, c = FakeProvider(['{"n": 1}'] * 5, "a"), FakeProvider(['{"n": 2}'] * 5, "b"), \
        FakeProvider(['{"n": 3}'] * 5, "c")
    cfg = LLMCfg(default="a", routes=route or {},
                 providers=[ProviderCfg(id=x, kind="openai", base_url="http://x/v1") for x in "abc"])
    st = SettingsStore(db)
    return LLMGateway(cfg, {"a": a, "b": b, "c": c}, override=st.llm_provider), st


def test_gateway_reads_settings_at_call_time(db):
    g, st = _gw(db, route={"script": "c"})
    assert g.generate("script", "s", "u", Out).n == 3          # config.yaml 的 routes
    st.save({"text_model": "b"})
    assert g.generate("script", "s", "u", Out).n == 2          # 全局默认盖过 config（不用重启）
    st.save({"step:script": "a"})
    assert g.generate("script", "s", "u", Out).n == 1          # 这一步单独设置最优先
    assert g.generate("storyboard", "s", "u", Out).n == 2
    # step 和任务名不同（角色相关三步共用 character 任务）
    st.save({"step:character.suggest": "c"})
    assert g.generate("character", "s", "u", Out, step="character.suggest").n == 3
    assert g.generate("character", "s", "u", Out, step="character.recommend").n == 2


def test_gateway_unknown_provider_in_settings_falls_back_and_logs(db):
    g, st = _gw(db)
    st.save({"text_model": "gone"})
    logs = []
    assert g.generate("script", "s", "u", Out, log=logs.append).n == 1
    assert any("gone" in m and "已失效" in m and "这次用 a" in m for m in logs)


# ---------------------------------------------------------------- API
def test_api_get_defaults(api):
    d = api.get("/api/settings").json()
    tm = d["text_model"]
    assert (tm["value"], tm["effective"], tm["fallback"], tm["source"]) == (None, "manual", "manual", "config")
    assert "手动" in tm["effective_label"] and tm["fallback_label"] == tm["effective_label"]
    assert d["image_engine"]["effective"] == "zimage" and d["video_engine"]["effective"] == "h3_video"
    steps = {x["key"]: x for x in d["steps"]}
    assert set(steps) == {"script", "storyboard", "character.suggest", "character.recommend",
                          "character.turnaround_look", "character.from_photo", "dialogue.fit_khmer",
                          "subtitles.translate", "portrait", "turnaround"}             # 首帧/视频跟项目走，没有步骤设置
    assert all(x["invalid"] is False for x in d["steps"])
    assert steps["script"]["label"] == "剧本" and steps["script"]["kind"] == "llm"
    assert steps["turnaround"]["effective"] == "h3_image" and steps["portrait"]["effective"] == "zimage"
    assert [p["id"] for p in d["choices"]["llm"]] == ["manual"]
    img = {e["name"]: e for e in d["choices"]["image"]}
    assert img["krea2"]["label"] == "Krea2 人像" and img["krea2"]["runtime"] == "comfyui"
    assert img["gpt_image"]["runtime"] == "browser" and img["zimage"]["ready"] is True
    assert {e["name"] for e in d["choices"]["video"]} == {"h3_video", "wan22_i2v"}


def test_api_put_validates_and_saves(api):
    r = api.put("/api/settings", json={"image_engine": "h3_video"})
    assert r.status_code == 400 and "图片来源" in r.json()["detail"]
    r = api.put("/api/settings", json={"text_model": "nope"})
    assert r.status_code == 400 and "nope" in r.json()["detail"]
    r = api.put("/api/settings", json={"steps": {"bogus": "x"}})
    assert r.status_code == 400
    r = api.put("/api/settings", json={"steps": {"turnaround": "h3_video"}})
    assert r.status_code == 400 and "三视图" in r.json()["detail"]
    r = api.put("/api/settings", json={"steps": {"keyframes": "zimage"}})
    assert r.status_code == 400 and "没有这个步骤" in r.json()["detail"]
    assert api.put("/api/settings", json={"image_engin": "krea2"}).status_code == 422   # 拼错的字段

    d = api.put("/api/settings", json={"image_engine": "krea2", "video_engine": "wan22_i2v",
                                       "steps": {"portrait": "gpt_image", "script": "manual"}}).json()
    assert d["image_engine"] == {"value": "krea2", "effective": "krea2", "fallback": "zimage",
                                 "source": "settings", "invalid": False}
    steps = {x["key"]: x for x in d["steps"]}
    assert steps["portrait"]["value"] == "gpt_image" and steps["portrait"]["effective"] == "gpt_image"
    assert steps["portrait"]["fallback"] == "krea2"           # 改回跟随默认后：跟（新项目的）图像引擎
    assert steps["turnaround"]["fallback"] == "h3_image"
    assert d["video_engine"]["effective"] == "wan22_i2v"
    assert steps["script"]["source"] == "step"
    # 只改传了的字段；null 回到跟随默认
    d = api.put("/api/settings", json={"steps": {"portrait": None}}).json()
    assert d["image_engine"]["value"] == "krea2"
    assert {x["key"]: x for x in d["steps"]}["portrait"]["value"] is None
    assert api.get("/api/settings").json()["image_engine"]["value"] == "krea2"


def test_new_project_defaults_from_settings(api):
    api.put("/api/settings", json={"image_engine": "krea2", "video_engine": "wan22_i2v"})
    p = api.post("/api/projects", json={"title": "新片"}).json()
    assert p["image_engine"] == "krea2" and p["video_engine"] == "wan22_i2v"
    p2 = api.post("/api/projects", json={"title": "指定", "image_engine": "zimage"}).json()
    assert p2["image_engine"] == "zimage"


def test_api_settings_for_project(api):
    api.post("/api/projects", json={"title": "片", "slug": "pp", "image_engine": "gpt_image",
                                    "video_engine": "wan22_i2v"})
    api.put("/api/settings", json={"image_engine": "krea2", "steps": {"turnaround": "zimage"}})
    d = api.get("/api/settings", params={"project": "pp"}).json()
    steps = {x["key"]: x for x in d["steps"]}
    assert d["project"] == "pp"
    assert steps["portrait"]["effective"] == "gpt_image"      # 没单独设置：跟项目
    assert steps["turnaround"]["effective"] == "zimage"       # 单独设置
    # 三视图的「默认（…）」也跟着设置走
    api.post("/api/projects/pp/characters", json={"name": "陈", "visual_prompt": "LOOK"})
    ch = api.get("/api/projects/pp/characters").json()[0]
    assert ch["turnaround"]["default_engine"] == "zimage"
    assert api.get("/api/settings", params={"project": "nope"}).status_code == 404


def test_api_engines_rows_have_label(api):
    rows = {e["name"]: e for e in api.get("/api/engines").json()["image"]}
    assert rows["zimage"]["label"] == "Z-Image 快速" and rows["gpt_image"]["label"] == "ChatGPT 网页"


# ---------------------------------------------------------------- 迁移
def test_migration_creates_app_settings(tmp_path):
    d = Database(tmp_path / "m.db")
    d.upgrade()
    SettingsStore(d).save({"text_model": "claude"})
    assert SettingsStore(d).load().text_model == "claude"
    d.engine.dispose()
    c = sqlite3.connect(d.path)
    try:
        ddl = c.execute("SELECT sql FROM sqlite_master WHERE name='app_settings'").fetchone()[0]
    finally:
        c.close()
    assert "key" in ddl and "value" in ddl


def test_api_portrait_draw_resolves_engine_once_in_route(api):
    """定妆照这一步设成网页引擎：路由就按它算队列（不占 GPU worker），payload 写死 engine，任务不再重新选。"""
    api.post("/api/projects", json={"title": "demo", "image_engine": "zimage"})
    ch = api.post("/api/projects/demo/characters", json={"name": "陈", "visual_prompt": "LOOK"}).json()
    api.put("/api/settings", json={"steps": {"portrait": "gpt_image"}})
    [jid] = api.post(f"/api/characters/{ch['id']}/portraits/draw", json={"count": 1}).json()["job_ids"]
    job = api.get(f"/api/jobs/{jid}").json()
    assert job["payload"]["engine"] == "gpt_image"
    assert job["model_family"] == "chatgpt_web" and job["gpu"] is False
    # 手选的最优先
    [jid] = api.post(f"/api/characters/{ch['id']}/portraits/draw",
                     json={"count": 1, "engine": "krea2"}).json()["job_ids"]
    job = api.get(f"/api/jobs/{jid}").json()
    assert job["payload"]["engine"] == "krea2" and job["model_family"] == "krea2"
    # 改回跟随默认：跟项目
    api.put("/api/settings", json={"steps": {"portrait": None}})
    [jid] = api.post(f"/api/characters/{ch['id']}/portraits/draw", json={"count": 1}).json()["job_ids"]
    assert api.get(f"/api/jobs/{jid}").json()["payload"]["engine"] == "zimage"


def test_api_marks_invalid_settings(api):
    """设置里存的 provider / 引擎不在了：标 invalid，生效值是退回去用的。"""
    store = api.app.state.services.settings
    store.save({"text_model": "gone", "image_engine": "old_engine", "step:portrait": "old_engine",
                "step:script": "gone"})
    d = api.get("/api/settings").json()
    assert d["text_model"]["invalid"] is True and d["text_model"]["effective"] == "manual"
    assert d["image_engine"]["invalid"] is True and d["image_engine"]["effective"] == "zimage"
    steps = {x["key"]: x for x in d["steps"]}
    assert steps["portrait"]["invalid"] is True and steps["portrait"]["effective"] == "zimage"
    assert steps["script"]["invalid"] is True and steps["script"]["effective"] == "manual"
    assert steps["storyboard"]["invalid"] is False
