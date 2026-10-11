"""从一个点子出片（首页向导）：出方向、扩写 / 合并 / 按要求改写、开始制作（建项目 + 第 1 集 + 剧本 → 分镜）、
继续出片（缺的定妆照 → 首帧 → 视频 → 导出）。"""
from backend.domain.models import Character, Episode, Shot
from backend.tests.helpers import make_gateway

OPT = {"title": "直播间的幽灵稿", "logline": "提词员发现提词器里多了一段不是她写的话", "hook": "主播念出一句没人写过的话",
       "twist": "写稿的是三年前去世的前任提词员", "ending": "提词器上出现了她自己的名字", "genre": "悬疑"}
OPT2 = {**OPT, "title": "老板的替身", "genre": "喜剧"}


def four(prefix="方向"):
    return {"options": [{**OPT, "title": f"{prefix}{i}", "genre": g}
                        for i, g in enumerate(["悬疑", "喜剧", "温情", "奇幻"], 1)]}


def use_llm(api, texts):
    gw, prov = make_gateway(texts)
    api.app.state.services.llm = gw
    return prov


def test_options_prompt_and_exclude(api):
    prov = use_llm(api, [four()])
    r = api.post("/api/ideas/options", json={"idea": "提词器被人改了", "target_seconds": 15,
                                             "style_preset": "anime", "feedback": "要有反转",
                                             "exclude": ["看过的方向A", "看过的方向B"]})
    assert r.status_code == 200, r.text
    opts = r.json()["options"]
    assert [o["title"] for o in opts] == ["方向1", "方向2", "方向3", "方向4"] and opts[0]["hook"]
    system, user = prov.calls[0]["system"], prov.calls[0]["messages"][0]["content"]
    assert "4 个" in system and "差异明显" in system and "15 秒" in system
    assert "看过的方向A" in system and "看过的方向B" in system and "要有反转" in system
    assert "提词器被人改了" in user


def test_options_retries_when_titles_repeat_or_all_seen(api):
    same = {"options": [OPT, OPT]}
    prov = use_llm(api, [same, four()])
    r = api.post("/api/ideas/options", json={"idea": "x"})
    assert r.status_code == 200 and len(prov.calls) == 2      # 第一次 title 重复，网关按 check 重试
    prov = use_llm(api, [four("旧"), four("新")])
    r = api.post("/api/ideas/options", json={"idea": "x", "exclude": [f"旧{i}" for i in range(1, 5)]})
    assert [o["title"] for o in r.json()["options"]][0] == "新1" and len(prov.calls) == 2


def test_options_empty_idea_and_llm_failure(api):
    assert api.post("/api/ideas/options", json={"idea": "  "}).status_code == 400
    use_llm(api, [{"oops": 1}] * 5)
    r = api.post("/api/ideas/options", json={"idea": "x"})
    assert r.status_code == 502 and "AI 出方案失败" in r.json()["detail"]


def test_expand_merge_and_revise(api):
    prov = use_llm(api, [{"title": " 幽灵稿 ", "synopsis": " 梗概一 "}, {"title": "幽灵稿", "synopsis": "梗概二"}])
    r = api.post("/api/ideas/expand", json={"idea": "提词器被人改了", "options": [OPT, OPT2], "target_seconds": 30})
    assert r.json() == {"title": "幽灵稿", "synopsis": "梗概一"}
    system, user = prov.calls[0]["system"], prov.calls[0]["messages"][0]["content"]
    assert "合成一个故事" in system and "直播间的幽灵稿" in user and "老板的替身" in user
    r = api.post("/api/ideas/expand", json={"idea": "x", "options": [OPT], "current": "梗概一",
                                            "instruction": "结尾更狠"})
    assert r.json()["synopsis"] == "梗概二"
    system, user = prov.calls[1]["system"], prov.calls[1]["messages"][0]["content"]
    assert "结尾更狠" in system and "梗概一" in user and "合成" not in system
    assert api.post("/api/ideas/expand", json={"idea": "x", "options": []}).status_code == 422


def test_start_creates_project_episode_and_queues_script_storyboard(api):
    body = {"title": "幽灵稿", "synopsis": "梗概正文", "target_seconds": 15, "style_preset": "anime",
            "image_engine": "zimage", "video_engine": "h3_video"}
    r = api.post("/api/ideas/start", json=body)
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["slug"] == "幽灵稿" and out["episode_number"] == 1 and len(out["job_ids"]) == 1
    job = api.app.state.manager.get(out["job_ids"][0])
    assert job["type"] == "script" and job["payload"]["then"] == ["storyboard"]   # 剧本跑完自己接分镜
    sv = api.app.state.services
    with sv.db.session() as s:
        ep = s.get(Episode, out["episode_id"])
        assert ep.source_text == "梗概正文" and ep.target_seconds == 15 and ep.title == "幽灵稿"
        assert ep.project.style_preset == "anime" and ep.project.title == "幽灵稿"
    # 同名再来一次：slug 加 -2
    assert api.post("/api/ideas/start", json=body).json()["slug"] == "幽灵稿-2"
    assert api.post("/api/ideas/start", json=body).json()["slug"] == "幽灵稿-3"


def _seed_storyboard(api, *, visual=True):
    r = api.post("/api/ideas/start", json={"title": "幽灵稿", "synopsis": "梗概", "image_engine": "zimage",
                                           "video_engine": "h3_video"})
    ep_id = r.json()["episode_id"]
    sv = api.app.state.services
    with sv.db.session() as s:
        ep = s.get(Episode, ep_id)
        pid = ep.project_id
        a = Character(project_id=pid, name="林夏", visual_prompt="短发女生，白衬衫" if visual else "")
        b = Character(project_id=pid, name="旁白", visual_prompt="", voice_only=True)
        s.add_all([a, b])
        s.flush()
        s.add(Shot(episode_id=ep_id, order=0, code="S01", action="林夏念稿", duration=5.0,
                   cast=[{"character_id": a.id}, {"character_id": b.id}]))
        s.add(Shot(episode_id=ep_id, order=1, code="S02", action="林夏回头", duration=5.0,
                   cast=[{"character_id": a.id}]))
        ids = (a.id, b.id)
    return ep_id, ids


def test_autopilot_portraits_then_keyframes_videos_export(api):
    ep_id, (lin, _narrator) = _seed_storyboard(api)
    r = api.post(f"/api/episodes/{ep_id}/autopilot")
    assert r.status_code == 200, r.text
    out = r.json()
    m = api.app.state.manager
    jobs = [m.get(j) for j in out["job_ids"]]
    portraits = [j for j in jobs if j["type"] == "image.portrait"]
    assert [j["payload"]["character_id"] for j in portraits] == [lin]     # 只出声的旁白不出定妆照
    kf = [j for j in jobs if j["type"] == "image.keyframe"]
    assert kf and all(portraits[0]["id"] in j["depends_on"] for j in kf)  # 首帧等定妆照
    assert any(j["type"] == "video.shot" for j in jobs) and jobs[-1]["type"] == "export.episode"
    assert out["skipped"] == [] and out["warnings"] == []


def test_autopilot_skips_character_without_visual_prompt_and_needs_storyboard(api):
    ep_id, _ = _seed_storyboard(api, visual=False)
    out = api.post(f"/api/episodes/{ep_id}/autopilot").json()
    assert not any(api.app.state.manager.get(j)["type"] == "image.portrait" for j in out["job_ids"])
    assert out["skipped"] and "林夏" in out["skipped"][0] and "外貌提示词" in out["skipped"][0]
    r = api.post("/api/ideas/start", json={"title": "空的", "synopsis": "x"})
    r = api.post(f"/api/episodes/{r.json()['episode_id']}/autopilot")
    assert r.status_code == 400 and "还没有分镜" in r.json()["detail"]
