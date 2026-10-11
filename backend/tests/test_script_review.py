"""剧本体检（script.review）：规则检查、LLM 体检任务、接口。"""
from types import SimpleNamespace

from backend.domain.models import Episode, Job, Location, SceneBlock, Shot
from backend.domain.shots import renumber_shots
from backend.domain.settings import LLM_STEPS
from backend.llm.prompts import PromptLibrary
from backend.pipeline.script_review import (rule_findings, run_script_review, timeline)
from backend.queue.errors import JobError
from backend.queue.handlers import HANDLERS
from backend.tests.helpers import FakeCtx, make_gateway, make_services, seed_episode

import pytest


def sh(code, duration, action="", lines=(), start=0.5):
    dialogue = [{"speaker": "林夏", "zh": z, "start": start, "end": start + 1.0} for z in lines]
    return SimpleNamespace(code=code, duration=duration, action=action, shot_size="中景",
                           dialogue=dialogue)


def by_key(findings):
    return {f.key: f for f in findings}


# ---------------------------------------------------------------- 规则检查
def test_timeline_accumulates_start_and_end():
    tl = timeline([sh("S01", 3), sh("S02", 5.5), sh("S03", 4)])
    assert [(x.code, x.start, x.end) for x in tl] == [("S01", 0, 3), ("S02", 3, 8.5), ("S03", 8.5, 12.5)]


def test_rules_good_episode_all_ok():
    tl = timeline([sh("S01", 3, "林夏被经理当众扇了一耳光", ["凭什么打我？"]),
                   sh("S02", 5, "经理冷笑", ["你被开除了"]),
                   sh("S03", 6, "林夏擦掉眼泪"),
                   sh("S04", 6, "门开了，董事长走进来", ["她是我女儿。"])])
    f = by_key(rule_findings(tl, 20))
    assert all(x.severity == "ok" for x in f.values()), [(x.key, x.severity, x.title) for x in f.values()]
    assert f["first_line"].title == "第一句台词在第 0.5 秒（S01）"
    assert f["hook_shots"].title == "前 15 秒有 4 个镜头"
    assert f["duration"].title.startswith("全集 20 秒 · 4 个镜头")


def test_rules_slow_opening_long_shot_and_flat_ending():
    tl = timeline([sh("S01", 8, "清晨的城市全景"), sh("S02", 12, "林夏走进公司", ["早上好"], start=1.0),
                   sh("S03", 6, "林夏坐下开电脑")])
    f = by_key(rule_findings(tl, 60))
    assert f["first_line"].severity == "warn" and "第 9 秒（S02）" in f["first_line"].title
    assert f["hook_conflict"].severity == "warn" and f["hook_conflict"].shots == ["S01", "S02"]
    assert f["hook_shots"].severity == "warn" and f["hook_shots"].title == "前 15 秒只有 2 个镜头"
    assert f["long_shots"].severity == "warn" and f["long_shots"].shots == ["S02"]
    assert f["ending"].severity == "warn" and f["ending"].shots == ["S03"]
    assert f["duration"].severity == "warn" and "目标 60 秒" in f["duration"].title


def test_rules_first_line_after_15s_is_error_and_no_dialogue_warns():
    late = timeline([sh("S01", 8, "空镜"), sh("S02", 8, "走廊"), sh("S03", 5, "", ["站住！"])])
    assert by_key(rule_findings(late, 21))["first_line"].severity == "error"
    silent = timeline([sh("S01", 5, "空镜"), sh("S02", 5, "走廊")])
    f = by_key(rule_findings(silent, 10))
    assert f["first_line"].severity == "warn" and f["first_line"].title == "全集没有一句台词"


def test_rules_ending_with_plain_line_is_info():
    tl = timeline([sh("S01", 3, "争吵", ["你骗我"]), sh("S02", 4, "林夏坐下", ["好的"])])
    assert by_key(rule_findings(tl, 7))["ending"].severity == "info"


def test_rules_without_shots():
    assert [(f.key, f.severity) for f in rule_findings([], 30)] == [("no_shots", "info")]


# ---------------------------------------------------------------- LLM 体检任务
REVIEW = {
    "hook": {"score": 4, "reason": "S01 是空镜，开口太晚"},
    "conflict": {"score": 6, "reason": "被开除有冲突，但没有反击"},
    "ending": {"score": 7, "reason": "停在董事长进门"},
    "pacing": {"score": 5, "reason": "S02 12 秒太长"},
    "summary": "开头要改",
    "suggestions": [
        {"dimension": "hook", "ref": "s1", "problem": "空镜开场", "rewrite": "经理：「你被开除了。」"},
        {"dimension": "pacing", "ref": "段落1", "problem": "拖", "rewrite": "拆成两镜"},
    ],
}


def seed_storyboard(db, shots=(("清晨的城市全景", 8, []), ("经理冷笑", 12, ["你被开除了"]))):
    proj_id, ep_id = seed_episode(db, target=20.0)
    with db.session() as s:
        loc = Location(project_id=proj_id, name="办公室")
        s.add(loc)
        s.flush()
        blk = SceneBlock(episode_id=ep_id, order=0, location_id=loc.id, text="经理当众开除林夏。")
        s.add(blk)
        s.flush()
        for i, (action, dur, lines) in enumerate(shots):
            s.add(Shot(episode_id=ep_id, scene_block_id=blk.id, order=i, action=action, duration=dur,
                       dialogue=[{"speaker": "经理", "zh": z, "start": 0.5, "end": 2.0} for z in lines]))
        s.flush()
        renumber_shots(s, ep_id)
    return proj_id, ep_id


def test_step_and_handler_registered():
    assert LLM_STEPS["script.review"] == "剧本体检"
    assert HANDLERS.get("script.review").gpu is False


def test_review_job_stores_scores_and_marks_stale_after_edit(tmp_path, db):
    gw, prov = make_gateway([REVIEW])
    sv = make_services(tmp_path, db, llm=gw)
    _, ep_id = seed_storyboard(db)
    out = run_script_review(FakeCtx(sv, job_id=7), {"episode_id": ep_id})
    assert out == {"scores": {"hook": 4, "conflict": 6, "ending": 7, "pacing": 5}, "suggestions": 2}
    user = prov.calls[0]["messages"][0]["content"]
    assert "S02 8–20s 段落 1" in user and "经理：「你被开除了」" in user
    assert "段落 1（办公室）：经理当众开除林夏。" in user
    assert "前 15 秒只有 2 个镜头" in prov.calls[0]["system"]   # 规则检查发现的问题一起给模型
    from backend.pipeline.script_review import stored_review

    with db.session() as s:
        ep = s.get(Episode, ep_id)
        data = ep.script_review
        assert data["job_id"] == 7 and data["inputs"]
        assert [x["ref"] for x in data["suggestions"]] == ["S01", "段落 1"]   # 引用写法统一
        assert stored_review(ep)["stale"] is False
        ep.shots[0].duration = 3.0
    with db.session() as s:
        assert stored_review(s.get(Episode, ep_id))["stale"] is True


def test_review_retries_unknown_ref_then_keeps_it_on_last_try(tmp_path, db):
    bad = {**REVIEW, "suggestions": [{"dimension": "ending", "ref": "S09", "rewrite": "加一句反问"}]}
    gw, prov = make_gateway([bad, bad, bad])
    sv = make_services(tmp_path, db, llm=gw)
    _, ep_id = seed_storyboard(db)
    run_script_review(FakeCtx(sv), {"episode_id": ep_id})
    assert len(prov.calls) == 3
    assert "S09" in prov.calls[1]["messages"][-1]["content"]
    with db.session() as s:
        assert s.get(Episode, ep_id).script_review["suggestions"][0]["ref"] == "S09"


def test_review_truncates_to_five_suggestions(tmp_path, db):
    many = {**REVIEW, "suggestions": [{"dimension": "hook", "ref": "S01", "rewrite": f"改 {i}"}
                                      for i in range(8)]}
    gw, _ = make_gateway([many])
    sv = make_services(tmp_path, db, llm=gw)
    _, ep_id = seed_storyboard(db)
    assert run_script_review(FakeCtx(sv), {"episode_id": ep_id})["suggestions"] == 5


def test_review_without_storyboard_fails_in_chinese(tmp_path, db):
    gw, prov = make_gateway([])
    sv = make_services(tmp_path, db, llm=gw)
    _, ep_id = seed_episode(db)
    with pytest.raises(JobError, match="还没有分镜"):
        run_script_review(FakeCtx(sv), {"episode_id": ep_id})
    assert prov.calls == []


def test_review_template_renders():
    system, user = PromptLibrary().render(
        "script_review", project_title="P", episode_title="", target_seconds=30.0, total_seconds=12.5,
        hook_seconds=15.0, blocks=[{"no": 1, "location": "", "text": "t"}], findings=[],
        shots=[{"code": "S01", "start_s": "0", "end_s": "3", "block": None, "shot_size": "",
                "action": "a", "said": ""}])
    assert "开场钩子" in system and "规则检查" not in system
    assert "分镜合计 12.5 秒，1 个镜头" in user and "S01 0–3s：a" in user


# ---------------------------------------------------------------- 接口
def api_seed(api, with_shots=True):
    api.post("/api/projects", json={"title": "demo"})
    ep = api.post("/api/projects/demo/episodes", json={"source_text": "x", "target_seconds": 20}).json()
    if with_shots:
        with api.app.state.services.db.session() as s:
            s.add(Shot(episode_id=ep["id"], order=0, action="空镜", duration=8.0))
            s.add(Shot(episode_id=ep["id"], order=1, action="经理冷笑", duration=12.0,
                       dialogue=[{"speaker": "经理", "zh": "你被开除了", "start": 1, "end": 2}]))
            s.flush()
            renumber_shots(s, ep["id"])
    return ep["id"]


def test_api_get_rules_without_llm_review(api):
    ep_id = api_seed(api)
    d = api.get(f"/api/episodes/{ep_id}/script-review").json()
    assert d["review"] is None and d["job"] is None
    assert d["rules"]["shot_count"] == 2 and d["rules"]["total_seconds"] == 20
    keys = {f["key"]: f for f in d["rules"]["findings"]}
    assert keys["long_shots"]["shots"] == ["S02"] and keys["long_shots"]["severity"] == "warn"
    assert api.get("/api/episodes/999/script-review").status_code == 404


def test_api_post_submits_once_and_needs_storyboard(api):
    ep_id = api_seed(api)
    r = api.post(f"/api/episodes/{ep_id}/script-review")
    assert r.status_code == 200, r.text
    jid = r.json()["job_ids"][0]
    again = api.post(f"/api/episodes/{ep_id}/script-review").json()
    assert again["job_ids"] == [jid] and again["notes"]          # 排队中不重复提交
    with api.app.state.services.db.session() as s:
        j = s.get(Job, jid)
        assert (j.type, j.owner_type, j.owner_id, j.payload, j.gpu) == (
            "script.review", "episode", ep_id, {"episode_id": ep_id}, False)
    assert api.get(f"/api/episodes/{ep_id}/script-review").json()["job"] == {
        "id": jid, "status": "queued", "error": None}
    empty = api_seed_second(api)
    r = api.post(f"/api/episodes/{empty}/script-review")
    assert r.status_code == 400 and "还没有分镜" in r.json()["detail"]


def api_seed_second(api):
    return api.post("/api/projects/demo/episodes", json={"source_text": "y"}).json()["id"]


def test_api_get_returns_stored_review(api):
    ep_id = api_seed(api)
    from backend.pipeline.script_review import episode_material, review_fp

    with api.app.state.services.db.session() as s:
        ep = s.get(Episode, ep_id)
        blocks, tl = episode_material(ep)
        ep.script_review = {**REVIEW, "inputs": review_fp(blocks, tl, ep.target_seconds),
                            "created_at": "2026-10-12T08:00:00", "job_id": 3}
    d = api.get(f"/api/episodes/{ep_id}/script-review").json()["review"]
    assert d["hook"] == {"score": 4, "reason": "S01 是空镜，开口太晚"}
    assert d["stale"] is False and "inputs" not in d and d["job_id"] == 3
