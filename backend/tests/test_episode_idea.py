"""新建剧集对话框的「AI 写梗概 / AI 续写」（POST /api/projects/{slug}/episode-idea）。"""
from backend.domain.settings import LLM_STEPS
from backend.pipeline.episode_idea import idea_mode, length_range
from backend.tests.helpers import make_gateway

LONG = ("林夏是直播间的提词员，今晚老板临时把稿子换了。她发现提词器里多了一段不是她写的话。"
        "她想删掉，却被导演按住了手。")


def setup_api(api, texts, *, preset="anime", episodes=()):
    r = api.post("/api/projects", json={"title": "提词员", "style_preset": preset,
                                        "image_engine": "zimage", "video_engine": "h3_video"})
    assert r.status_code == 201, r.text
    slug = r.json()["slug"]
    for n, title, src in episodes:
        api.post(f"/api/projects/{slug}/episodes", json={"number": n, "title": title, "source_text": src})
    gw, prov = make_gateway(texts)
    api.app.state.services.llm = gw
    return slug, prov


def test_mode_detection_and_length():
    assert idea_mode("  ") == "new"
    assert idea_mode("女主重生回到订婚宴前一天") == "expand"
    assert idea_mode("女主重生回到订婚宴前一天。") == "expand"
    assert idea_mode(LONG) == "continue"
    assert idea_mode("第一句。第二句") == "continue"
    lo10, hi10 = length_range(10)
    lo60, hi60 = length_range(60)
    assert hi10 <= 100 and lo60 >= 150 and hi60 > hi10


def test_new_from_title_uses_preset_seconds_and_previous(api):
    slug, prov = setup_api(api, [{"text": "  订婚宴上，林夏当众撕了婚书……  "}], preset="guofeng",
                           episodes=[(1, "开端", "第一集：林夏被退婚，雨夜里捡到一块玉佩。"),
                                     (2, "", "第二集：玉佩里住着一个剑灵，说她十年前就死过一次。")])
    r = api.post(f"/api/projects/{slug}/episode-idea",
                 json={"title": "撕婚书", "target_seconds": 45, "text": ""})
    assert r.status_code == 200, r.text
    assert r.json() == {"text": "订婚宴上，林夏当众撕了婚书……", "mode": "new"}
    call = prov.calls[0]
    system, user = call["system"], call["messages"][0]["content"]
    assert "国风漫画" in system and "45 秒" in system
    assert "反转" in system and "不写分镜" in system
    assert "第 3 集" in user and "撕婚书" in user                 # 没给集号：接在最后一集后面
    assert "雨夜里捡到一块玉佩" in user and "剑灵" in user          # 前情
    assert "前情" in system


def test_expand_one_line_idea(api):
    slug, prov = setup_api(api, [{"text": "扩写后的梗概"}], preset="live_action")
    r = api.post(f"/api/projects/{slug}/episode-idea",
                 json={"title": "", "target_seconds": 10, "number": 1, "text": "女主重生回到订婚宴前一天"})
    assert r.json() == {"text": "扩写后的梗概", "mode": "expand"}
    system, user = prov.calls[0]["system"], prov.calls[0]["messages"][0]["content"]
    assert "真人写实" in system and "10 秒" in system and "2–4 句" in system
    assert "女主重生回到订婚宴前一天" in user and "扩写" in user
    assert "前情" not in user


def test_continue_returns_only_new_part(api):
    # 模型把用户写的又抄了一遍：去掉重复的开头，只返回新增部分
    slug, prov = setup_api(api, [{"text": LONG + "导演低声说：念出来，否则你妹妹回不了家。"}],
                           preset="commercial")
    r = api.post(f"/api/projects/{slug}/episode-idea",
                 json={"target_seconds": 60, "number": 1, "text": LONG})
    assert r.json() == {"text": "导演低声说：念出来，否则你妹妹回不了家。", "mode": "continue"}
    system, user = prov.calls[0]["system"], prov.calls[0]["messages"][0]["content"]
    assert "商业广告" in system and "行动号召" in system and "接着往下写" in system
    assert LONG in user


def test_empty_without_title_or_previous_is_400(api):
    slug, prov = setup_api(api, [])
    r = api.post(f"/api/projects/{slug}/episode-idea", json={"target_seconds": 30})
    assert r.status_code == 400 and "标题" in r.json()["detail"]
    assert prov.calls == []


def test_llm_failure_gives_chinese_error(api):
    slug, prov = setup_api(api, ["不是 JSON", "还不是", "依然不是"])
    r = api.post(f"/api/projects/{slug}/episode-idea", json={"title": "撕婚书"})
    assert r.status_code == 502 and r.json()["detail"].startswith("AI 写梗概失败")
    assert len(prov.calls) == 3
    r = api.post("/api/projects/nope/episode-idea", json={"title": "x"})
    assert r.status_code == 404


def test_manual_provider_tells_to_pick_model(api):
    # 测试配置默认是手动粘贴：同步接口没法等人粘贴，提示去设置页选模型
    r = api.post("/api/projects", json={"title": "手动", "image_engine": "zimage", "video_engine": "h3_video"})
    slug = r.json()["slug"]
    r = api.post(f"/api/projects/{slug}/episode-idea", json={"title": "撕婚书"})
    assert r.status_code == 400 and "手动粘贴" in r.json()["detail"] and "AI 写梗概" in r.json()["detail"]


def test_step_listed_in_settings(api):
    assert "episode.idea" in LLM_STEPS
    steps = {x["key"]: x for x in api.get("/api/settings").json()["steps"]}
    assert steps["episode.idea"]["kind"] == "llm"
