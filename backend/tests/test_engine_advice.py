"""按镜头推荐视频引擎：backend/domain/engine_advice.py 和两个 engine-advice 接口。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.domain.engine_advice import (EngineInfo, advise_episode, advise_shot, big_actions, camera_level,
                                          difficulty_of, shot_difficulty)
from backend.domain.models import Shot
from backend.engines.base import VideoCaps

SIZES = {"9:16": (720, 1280)}


def caps(*, audio=True, end=False, refs=False, top=10.0):
    return VideoCaps(max_refs=9 if refs else 0, supports_end_frame=end, has_audio=audio, fps=24, sizes=SIZES,
                     default_seconds=5.0, max_seconds=top, ref_mode=refs)


# 一套典型的引擎：本机 H3（标准、有声）、本机 Wan（标准、没声音、最长 5 秒）、即梦网页（强、尾帧）、
# Seedance API（强、尾帧、参考图直出、15 秒）、可灵 API（强、尾帧）
ENGINES = [
    EngineInfo("h3_video", "H3 视频", "comfyui", caps(top=10.0)),
    EngineInfo("wan22_i2v", "Wan 2.2", "comfyui", caps(audio=False, top=5.0)),
    EngineInfo("jimeng_video", "即梦网页", "browser", caps(end=True, top=15.0)),
    EngineInfo("seedance_video", "Seedance API", "api", caps(end=True, refs=True, top=15.0)),
    EngineInfo("kling_video", "可灵 API", "api", caps(end=True, top=15.0)),
]


def shot(**kw):
    base = dict(id=1, code="S01", shot_size="近景", camera_move="static", action="她看着窗外", motion_prompt="",
                cast=[{"character_id": 1}], dialogue=[], props=[], duration=5.0, continue_from_prev=False,
                use_end_frame=False, video_mode=None, video_engine=None)
    base.update(kw)
    return SimpleNamespace(**base)


def names(adv):
    return [r.engine for r in adv.recommended]


@pytest.mark.parametrize("kw, difficulty, reason_bits", [
    ({}, "easy", []),
    ({"cast": [{}, {}]}, "medium", ["两人同框"]),
    ({"cast": [{}, {}, {}]}, "medium", ["多人同框（3 人）"]),
    ({"action": "两人在巷子里打斗", "cast": [{}, {}]}, "hard", ["两人同框", "大动作（打斗）"]),
    ({"motion_prompt": "he is running and jumps over a fence"}, "medium", ["大动作（running、jumps）"]),
    ({"camera_move": "slow push in"}, "easy", ["有运镜（slow push in）"]),
    ({"camera_move": "环绕跟拍", "cast": [{}, {}, {}]}, "hard", ["复杂运镜（环绕跟拍）", "多人同框"]),
    ({"dialogue": [{"speaker": "林夏", "zh": "走吧"}]}, "easy", ["有台词"]),
    ({"duration": 10.0, "props": ["a", "b"]}, "medium", ["时长 10 秒", "道具多（2 件）"]),
])
def test_difficulty_table(kw, difficulty, reason_bits):
    adv = advise_shot(shot(**kw), ENGINES, "h3_video")
    assert adv.difficulty == difficulty
    for bit in reason_bits:
        assert any(bit in r for r in adv.reasons), (bit, adv.reasons)


def test_keyword_helpers():
    assert set(big_actions("他狂奔着追赶小偷")) == {"追赶", "狂奔"}
    assert big_actions("a quiet room, she sips tea; rerun, fighter") == []   # 英文按词边界，不认词的一部分
    assert big_actions("Sword FIGHT, fight") == ["sword", "fight"]
    assert [camera_level(x) for x in ("", "固定", "Static", "push in", "orbit around", "航拍")] == [0, 0, 0, 1, 2, 2]
    assert [difficulty_of(n) for n in (0, 1, 2, 3, 4, 9)] == ["easy", "easy", "medium", "medium", "hard", "hard"]
    assert shot_difficulty(shot())[0] == 0


@pytest.mark.parametrize("kw, current, want_first, ok, problem_bits, switch", [
    # 简单镜头：本机优先（标准档在前），当前 H3 合适，不建议换
    ({}, "h3_video", "h3_video", True, [], False),
    # 简单镜头用了按量付费的 API：建议换便宜的
    ({}, "kling_video", "h3_video", True, [], True),
    # 有台词：Wan 没声音，不推荐，当前是 Wan 时报问题
    ({"dialogue": [{"speaker": "a", "zh": "嗯"}]}, "wan22_i2v", "h3_video", False, ["没有声音"], True),
    # 用尾帧：只推荐支持尾帧的，网页额度先于 API
    ({"use_end_frame": True}, "h3_video", "jimeng_video", False, ["不支持尾帧"], True),
    # 参考图直出：只有 Seedance 能用
    ({"video_mode": "refs"}, "h3_video", "seedance_video", False, ["不支持参考图直出"], True),
    # 接上一镜时参考图直出不生效，不算硬性要求
    ({"video_mode": "refs", "continue_from_prev": True}, "h3_video", "h3_video", True, [], False),
    # 时长超过上限
    ({"duration": 12.0}, "h3_video", "jimeng_video", False, ["最长 10 秒"], True),
    # 难镜头：强模型优先（网页额度先于 API），当前标准档 → 建议换
    ({"action": "三人打斗", "cast": [{}, {}, {}]}, "h3_video", "jimeng_video", True, [], True),
    # 难镜头已经在用强模型：不建议换
    ({"action": "三人打斗", "cast": [{}, {}, {}]}, "kling_video", "jimeng_video", True, [], False),
    # 中等难度：便宜的先
    ({"cast": [{}, {}]}, "seedance_video", "h3_video", True, [], False),
])
def test_ranking_and_current(kw, current, want_first, ok, problem_bits, switch):
    adv = advise_shot(shot(**kw), ENGINES, current)
    assert names(adv)[0] == want_first, names(adv)
    assert adv.current_ok is ok
    for bit in problem_bits:
        assert any(bit in p for p in adv.current_problems), adv.current_problems
    assert adv.suggest_switch is switch, adv.switch_reason
    assert len(adv.recommended) <= 3 and all(r.why for r in adv.recommended)
    assert {c.engine for c in adv.checks} == {e.name for e in ENGINES}


def test_not_ready_engines_skipped_and_current_not_ready():
    engines = [EngineInfo("h3_video", "H3", "comfyui", caps(), ready=False, note="ComfyUI 没有连上"),
               *ENGINES[1:]]
    adv = advise_shot(shot(), engines, "h3_video")
    assert "h3_video" not in names(adv) and names(adv)[0] == "wan22_i2v"
    assert adv.current_ok is True and adv.suggest_switch is True and "ComfyUI 没有连上" in adv.switch_reason
    h3 = next(c for c in adv.checks if c.engine == "h3_video")
    assert h3.ready is False and h3.note == "ComfyUI 没有连上" and h3.ok is True


def test_unknown_or_missing_current_and_nothing_usable():
    adv = advise_shot(shot(), ENGINES, "gone_video")
    assert adv.current_ok is False and "gone_video" in adv.current_problems[0] and adv.suggest_switch
    adv = advise_shot(shot(), ENGINES, None)
    assert adv.current_ok is False and "没有选视频引擎" in adv.current_problems[0]
    # 没有能用的：不推荐，也不建议换（换不了）
    adv = advise_shot(shot(video_mode="refs"), ENGINES[:3], "h3_video")
    assert adv.recommended == [] and adv.current_ok is False and adv.suggest_switch is False


def test_unknown_engine_is_standard_tier():
    rh = EngineInfo("rh_wan", "RH Wan", "runninghub", caps())
    assert rh.tier == "standard" and rh.cost == 2


def test_advise_episode_uses_project_default():
    shots = [shot(id=1, code="S01"), shot(id=2, code="S02", video_engine="kling_video"),
             shot(id=3, code="S03", dialogue=[{"speaker": "a", "zh": "嗯"}])]
    ep = advise_episode(7, shots, ENGINES, "wan22_i2v")
    assert ep.episode_id == 7 and [a.code for a in ep.shots] == ["S01", "S02", "S03"]
    assert [a.current_engine for a in ep.shots] == ["wan22_i2v", "kling_video", "wan22_i2v"]
    # S01 Wan 合适（便宜的都在本机）；S02 简单镜头用 API → 换；S03 有台词 Wan 没声音 → 换
    assert [a.suggest_switch for a in ep.shots] == [False, True, True] and ep.switch_count == 2


# ---------------------------------------------------------------- 接口
def _seed(api):
    api.post("/api/projects", json={"title": "demo", "image_engine": "zimage", "video_engine": "h3_video"})
    ep = api.post("/api/projects/demo/episodes", json={"source_text": "x"}).json()
    db = api.app.state.services.db
    with db.session() as s:
        a = Shot(episode_id=ep["id"], order=0, code="S01", action="她坐着", camera_move="static")
        b = Shot(episode_id=ep["id"], order=1, code="S02", action="两人在雨里打斗", camera_move="环绕",
                 cast=[{"character_id": 1}, {"character_id": 2}, {"character_id": 3}],
                 dialogue=[{"speaker": "x", "zh": "住手", "start": 0.2, "end": 1.0}], use_end_frame=True)
        s.add_all([a, b])
        s.flush()
        return ep["id"], a.id, b.id


def test_engine_advice_routes(api):
    ep_id, a, b = _seed(api)
    r = api.get(f"/api/shots/{b}/engine-advice")
    assert r.status_code == 200
    body = r.json()
    assert body["shot_id"] == b and body["code"] == "S02" and body["difficulty"] == "hard"
    assert body["current_engine"] == "h3_video" and body["current_ok"] is False
    assert any("不支持尾帧" in p for p in body["current_problems"])
    assert any("多人同框" in x for x in body["reasons"]) and any("尾帧" in x for x in body["requirements"])
    # 推荐的都满足要求（有声 + 尾帧）且能用
    checks = {c["engine"]: c for c in body["checks"]}
    for rec in body["recommended"]:
        assert checks[rec["engine"]]["ok"] and checks[rec["engine"]]["ready"] and rec["why"]
    assert checks["h3_video"]["tier"] == "standard" and checks["h3_video"]["runtime"] == "comfyui"

    r = api.get(f"/api/episodes/{ep_id}/engine-advice")
    assert r.status_code == 200
    body = r.json()
    assert [x["code"] for x in body["shots"]] == ["S01", "S02"]
    assert body["switch_count"] == sum(1 for x in body["shots"] if x["suggest_switch"])
    assert body["shots"][0]["difficulty"] == "easy"
    assert api.get("/api/shots/9999/engine-advice").status_code == 404
    assert api.get("/api/episodes/9999/engine-advice").status_code == 404
