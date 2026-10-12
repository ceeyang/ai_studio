"""固定时长视频引擎第 1 步（设计文档 docs/specs/2026-10-12-fixed-length-engines-design.md）：
分镜按引擎时长规划、多拍镜头（beats）、项目选项、网页引擎参考图模式、浪费提醒。"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.domain.beats import (check_beats, effective_pack, generation_unit, scale_beats, snap_seconds,
                                  waste_seconds)
from backend.domain.engine_advice import EngineInfo, advise_shot
from backend.domain.models import Character, Episode, Project, SceneBlock, Shot
from backend.domain.shots import renumber_shots
from backend.domain.slots import add_candidate, get_or_create_slot
from backend.engines.base import VideoCaps, VideoRequest
from backend.engines.browser.engines import GeminiVideoEngine, MuseVideoEngine, web_video_prompt
from backend.llm.schemas import ShotOut
from backend.pipeline.prompting import (BEATS_INSTRUCTION, Speaker, compose_video_prompt,
                                        video_input_hash)
from backend.pipeline.storyboard import build_storyboard_plan, run_storyboard
from backend.pipeline.videos import qc_bad, scene_qc
from backend.tests.helpers import FakeCtx, make_gateway, make_services
from backend.tests.test_storyboard import seed_scripted, shots_json

GEMINI = GeminiVideoEngine.caps
MUSE = MuseVideoEngine.caps
PARAM = VideoCaps(max_refs=0, supports_end_frame=False, has_audio=True, fps=24, sizes={"9:16": (1, 1)},
                  default_seconds=5.0, max_seconds=15.0, duration_control="param",
                  allowed_seconds=(5.0, 10.0, 15.0))

BEATS3 = [{"start": 0, "end": 3.5, "shot_size": "中景", "camera_move": "static",
           "action": "The young woman sets the cup down"},
          {"start": 3.5, "end": 6.5, "shot_size": "近景", "camera_move": "slow push in",
           "action": "The young woman looks up toward the door"},
          {"start": 6.5, "end": 10, "shot_size": "特写", "camera_move": "static",
           "action": "Close on the cup as steam rises"}]


# ------------------------------------------------------------ 生成单元、档位、浪费
def test_generation_unit_and_waste():
    assert generation_unit(GEMINI) == 10.0 and generation_unit(GEMINI, 3) == 10.0
    assert generation_unit(MUSE) == 10.0                   # 分镜默认：不超过 10 秒的最大档
    assert generation_unit(MUSE, 4) == 5.0                 # 镜头时长：能覆盖的最小档
    assert generation_unit(PARAM) is None
    assert waste_seconds(GEMINI, 3) == (10.0, 7.0)
    assert waste_seconds(GEMINI, 9) is None                # 只浪费 1 秒，不提醒
    assert waste_seconds(GEMINI, 3, beats=BEATS3) is None  # 多拍镜头不提醒
    assert waste_seconds(MUSE, 3) is None and waste_seconds(MUSE, 6) == (10.0, 4.0)
    assert waste_seconds(PARAM, 3) is None
    assert snap_seconds(6.4, (5, 10, 15)) == 5 and snap_seconds(8, (5, 10, 15)) == 10
    assert snap_seconds(4.0, (5, 10), lo=5.5) == 10        # 不短于台词说完
    assert effective_pack("beats") == "beats" and effective_pack("off") == "off"
    assert effective_pack("group") == "group"              # 组镜拆分（第 2 步）
    assert effective_pack(None) == "beats"


# ------------------------------------------------------------ beats 校验
def test_check_beats_rules():
    assert check_beats(None, 10) is None and check_beats([], 10) is None
    out = check_beats([{**BEATS3[0], "end": 3.4}, {**BEATS3[1], "start": 3.5}, BEATS3[2]], 10)
    assert out[1]["start"] == 3.4 and out[-1]["end"] == 10    # 误差内的缝对齐
    with pytest.raises(ValueError, match="2–4 拍"):
        check_beats(BEATS3[:1], 3.5)
    with pytest.raises(ValueError, match="至少 1.5 秒"):
        check_beats([{**BEATS3[0], "end": 1.0}, {**BEATS3[1], "start": 1.0, "end": 10}], 10)
    with pytest.raises(ValueError, match="要接上第 1 拍"):
        check_beats([BEATS3[0], {**BEATS3[1], "start": 4.5}, BEATS3[2]], 10)
    with pytest.raises(ValueError, match="最后一拍"):
        check_beats(BEATS3, 8)
    with pytest.raises(ValueError, match="action"):
        check_beats([BEATS3[0], {**BEATS3[1], "action": " "}, BEATS3[2]], 10)
    scaled = scale_beats(BEATS3, 9)
    assert scaled[-1]["end"] == 9 and scaled[1]["start"] == scaled[0]["end"]


def _shot_out(**kw):
    img = "medium close-up, 85mm, eye level. " + " ".join(["detail"] * 60)
    return ShotOut(**{"action": "x", "image_prompt": img, "motion_prompt": "m", "duration": 10, **kw})


def test_shot_out_validates_beats_in_chinese():
    assert len(_shot_out(beats=BEATS3).beats) == 3
    assert _shot_out(beats=[]).beats is None
    with pytest.raises(ValueError, match="最后一拍要在镜头结束时结束"):
        _shot_out(beats=BEATS3, duration=8)
    with pytest.raises(ValueError, match="2–4 拍"):
        _shot_out(beats=[dict(b, start=i * 2, end=i * 2 + 2) for i, b in enumerate(BEATS3 * 2)][:5])


# ------------------------------------------------------------ 分镜规划
def beats_json(n, duration=10):
    out = shots_json(n, duration=duration)
    half = duration / 2
    for sh in out["shots"]:
        sh["beats"] = [dict(b) for b in BEATS3] if duration == 10 else \
            [dict(BEATS3[0], end=half), dict(BEATS3[1], start=half, end=duration)]
        if duration == 10 and sh["dialogue"]:
            sh["dialogue"] = [{"speaker": "林夏", "zh": "词被改了", "start": 4.0, "end": 6.0}]
    return out


def _plan(tmp_path, db, engine, pack="beats", target=20.0, pace=None):
    ep_id = seed_scripted(db, video_engine=engine)
    with db.session() as s:
        ep = s.get(Episode, ep_id)
        ep.target_seconds = target
        proj = s.get(Project, ep.project_id)
        proj.fixed_engine_pack = pack
        proj.shot_seconds = pace
    sv = make_services(tmp_path, db)
    with db.session() as s:
        return build_storyboard_plan(s, sv, ep_id), ep_id


def test_plan_fixed_engine_beats_mode(tmp_path, db):
    plan, _ = _plan(tmp_path, db, "gemini_video", target=45.0, pace=3)
    # 每镜是一次生成：45 秒 ÷ 10 秒向上取整 = 5 镜，每镜 9 秒（不超过 10）；项目节奏 3 秒不管用
    assert plan.beats_mode and plan.shot_seconds == 10.0
    assert sum(b["count"] for b in plan.blocks) == 5
    assert all(b["seconds"] / b["count"] <= 10 for b in plan.blocks)
    assert plan.duration_plan == {"mode": "beats", "control": "fixed", "unit": 10.0, "allowed": [10.0]}


def test_plan_prompt_engine_uses_largest_allowed_up_to_10(tmp_path, db):
    plan, _ = _plan(tmp_path, db, "muse_video")
    assert plan.duration_plan["unit"] == 10.0 and plan.duration_plan["control"] == "prompt"


def test_plan_pack_off_keeps_old_behaviour(tmp_path, db):
    plan, _ = _plan(tmp_path, db, "gemini_video", pack="off", pace=3)
    assert plan.duration_plan is None and plan.shot_seconds == 3.0
    assert sum(b["count"] for b in plan.blocks) == 7      # 20 ÷ 3，老算法


def test_plan_group_pack(tmp_path, db):
    """组镜模式：每镜照常按节奏（没设时 4 秒、最长 8 秒），分好后再分组（test_clip_groups.py）。"""
    plan, _ = _plan(tmp_path, db, "gemini_video", pack="group")
    assert plan.duration_plan == {"mode": "group", "control": "fixed", "unit": 10.0, "allowed": [10.0],
                                  "pace": 4.0}
    assert plan.shot_seconds == 4.0 and plan.max_seconds == 8.0 and plan.group_mode


def test_plan_param_engine_snaps_to_allowed(tmp_path, db):
    plan, _ = _plan(tmp_path, db, "jimeng_video", pace=4)
    assert plan.duration_plan == {"mode": "snap", "allowed": [5.0, 10.0, 15.0]}
    assert plan.shot_seconds == 4.0                        # 节奏照常（项目镜头节奏优先）


def _sb(tmp_path, db, texts, engine, pack="beats", target=20.0):
    gw, prov = make_gateway(texts)
    ctx = FakeCtx(make_services(tmp_path, db, llm=gw))
    ep_id = seed_scripted(db, video_engine=engine)
    with db.session() as s:
        ep = s.get(Episode, ep_id)
        ep.target_seconds = target
        s.get(Project, ep.project_id).fixed_engine_pack = pack
    return ctx, ep_id, prov


def test_storyboard_beats_mode_prompt_and_persist(tmp_path, db):
    ctx, ep_id, prov = _sb(tmp_path, db, [beats_json(1), beats_json(1)], "gemini_video")
    run_storyboard(ctx, {"episode_id": ep_id})
    system = prov.calls[0]["system"]
    assert "每次固定出 10 秒" in system and "beats" in system and '"beats": [{"start": 0' in system
    with db.session() as s:
        shots = s.get(Episode, ep_id).shots
        assert [x.duration for x in shots] == [10.0, 10.0]
        assert [len(x.beats) for x in shots] == [3, 3]
        assert shots[0].beats[-1]["end"] == 10.0
        rec = s.get(Episode, ep_id).stage_hashes[f"storyboard:{shots[0].scene_block_id}"]
        assert rec["plan"]["mode"] == "beats"


def test_storyboard_beats_mode_requires_beats_on_long_shots(tmp_path, db):
    plain = shots_json(1, duration=10)
    ctx, ep_id, prov = _sb(tmp_path, db, [plain, beats_json(1), beats_json(1)], "gemini_video")
    run_storyboard(ctx, {"episode_id": ep_id})
    assert "没有写 beats" in prov.calls[1]["messages"][2]["content"]


def test_storyboard_off_mode_drops_beats_and_uses_old_prompt(tmp_path, db):
    # h3 能精确控时长：老行为（每镜 5 秒，20 秒 4 镜），分镜就算写了 beats 也不存
    ctx, ep_id, prov = _sb(tmp_path, db, [beats_json(2, duration=5), beats_json(2, duration=5)],
                           "wan22_i2v", pack="beats")
    run_storyboard(ctx, {"episode_id": ep_id})
    assert "每次固定出" not in prov.calls[0]["system"]
    with db.session() as s:
        assert all(x.beats is None for x in s.get(Episode, ep_id).shots)


def test_storyboard_param_engine_snaps_durations(tmp_path, db):
    ctx, ep_id, prov = _sb(tmp_path, db, [shots_json(2, duration=6), shots_json(2, duration=6)],
                           "jimeng_video")
    run_storyboard(ctx, {"episode_id": ep_id})
    assert "只认 5 / 10 / 15 秒这几档" in prov.calls[0]["system"]
    with db.session() as s:
        assert [x.duration for x in s.get(Episode, ep_id).shots] == [5.0] * 4


def test_plan_change_only_hints_and_never_reruns(tmp_path, db):
    """老记录（没有 "plan"）按老规划比指纹：换成多拍规划只提示，不自动删镜头重出。"""
    # 45 秒：老规划每镜 10 秒 round(4.5) = 4 镜，多拍规划向上取整 5 镜——镜头数变了也不重跑
    ctx, ep_id, prov = _sb(tmp_path, db, [shots_json(2, duration=10)] * 2, "gemini_video", pack="off",
                           target=45.0)
    run_storyboard(ctx, {"episode_id": ep_id})
    with db.session() as s:
        ep = s.get(Episode, ep_id)
        # 模拟第 1 步之前的记录：没有 "plan"
        ep.stage_hashes = {k: {kk: vv for kk, vv in v.items() if kk != "plan"}
                           for k, v in ep.stage_hashes.items()}
        s.get(Project, ep.project_id).fixed_engine_pack = "beats"
    assert run_storyboard(ctx, {"episode_id": ep_id}) == {"blocks_done": 0, "blocks_skipped": 2}
    assert any("时长规划变了" in m for m in ctx.logs)
    assert len(prov.calls) == 2


# ------------------------------------------------------------ 视频提示词
def test_timestamped_video_prompt():
    speakers = {"林夏": Speaker("the young woman", True, "female")}
    dialogue = [{"speaker": "林夏", "zh": "你来了", "start": 4.0, "end": 5.5}]
    text = compose_video_prompt("she waits", "img", "rain on the window. No background music.", dialogue,
                                anchor="ANCHOR", has_audio=True, speakers=speakers, beats=BEATS3,
                                reminder="Same people: the young woman (woman in her late twenties)")
    lines = text.split("\n")
    assert lines[0].startswith("[00:00-00:04] Medium shot, static: The young woman sets the cup down.")
    assert lines[0].count("Same people: the young woman") == 1
    assert "你来了" in lines[1] and "你来了" not in lines[0] and lines[1].startswith("[00:04-00:07]")
    assert lines[2].startswith("[00:07-00:10] Close-up, static:")
    assert BEATS_INSTRUCTION in text and "No background music." in text
    # 没有 beats 的镜头和以前一字不差
    assert compose_video_prompt("m", "i", "a", [], anchor="A", has_audio=True) == \
        compose_video_prompt("m", "i", "a", [], anchor="A", has_audio=True, beats=None)


def test_video_hash_includes_beats():
    shot = SimpleNamespace(motion_prompt="m", image_prompt="i", audio_prompt="", dialogue=[], duration=10,
                           beats=None, cast=[], shot_size="")
    proj = SimpleNamespace(aspect="9:16", characters=[])
    style = SimpleNamespace(anchor_text="A")
    plain = video_input_hash(shot, proj, style, 1, "gemini_video", True)
    shot.beats = BEATS3
    with_beats = video_input_hash(shot, proj, style, 1, "gemini_video", True)
    shot.beats = [dict(BEATS3[0], action="changed"), *BEATS3[1:]]
    assert len({plain, with_beats, video_input_hash(shot, proj, style, 1, "gemini_video", True)}) == 3


# ------------------------------------------------------------ 质检
class _Ctx:
    def __init__(self):
        self.logs = []

    def log(self, m):
        self.logs.append(m)


def test_scene_qc_ignores_cuts_on_beat_boundaries(tmp_path):
    sv = SimpleNamespace(ffmpeg=SimpleNamespace(scene_cuts=lambda p: [3.2, 5.0, 6.9]))
    ctx = _Ctx()
    qc = scene_qc(ctx, sv, tmp_path / "v.mp4", "S01", beats=BEATS3)
    assert qc["hard_cuts"] == [5.0] and qc["beat_cuts"] == [3.2, 6.9]
    qc = scene_qc(ctx, sv, tmp_path / "v.mp4", "S01", beats=[BEATS3[0], dict(BEATS3[1], end=10)])
    assert qc["hard_cuts"] == [5.0, 6.9]
    sv = SimpleNamespace(ffmpeg=SimpleNamespace(scene_cuts=lambda p: [3.5]))
    qc = scene_qc(ctx, sv, tmp_path / "v.mp4", "S01", beats=BEATS3)
    assert not qc_bad(qc)


# ------------------------------------------------------------ 网页引擎参考图模式
def test_web_video_prompt_first_frame_vs_refs():
    ff = web_video_prompt("x", "9:16", None, first_frame=True)
    assert "as the first frame" in ff
    refs = web_video_prompt("x", "9:16", None, first_frame=False, n_refs=2)
    assert "not the first frame" in refs and "as the first frame" not in refs


@pytest.mark.parametrize("cls", [GeminiVideoEngine, MuseVideoEngine])
def test_web_engine_build_refs_and_first_frame(cls):
    eng = cls()
    refs = [Path(f"/tmp/r{i}.png") for i in range(4)]
    call = eng.build(VideoRequest(prompt="p", first_frame=None, aspect="9:16", refs=refs))
    assert call.workflow["refs"] == [str(p) for p in refs[:3]]       # 上限 3 张（待实测）
    assert call.dropped_refs == [str(refs[3])] and "not the first frame" in call.workflow["prompt"]
    assert call.meta["first_frame"] is None
    call = eng.build(VideoRequest(prompt="p", first_frame=Path("/tmp/ff.png"), aspect="9:16", refs=refs[:2]))
    assert call.workflow["refs"] == ["/tmp/ff.png"] and "as the first frame" in call.workflow["prompt"]
    assert call.dropped_refs == [str(p) for p in refs[:2]]


# ------------------------------------------------------------ 引擎建议
def test_engine_advice_reports_waste():
    shot = SimpleNamespace(id=1, code="S01", cast=[], action="", motion_prompt="", camera_move="",
                           dialogue=[], props=[], duration=3.0, use_end_frame=False, beats=None)
    infos = [EngineInfo("gemini_video", "Gemini 网页（Veo）", "browser", GEMINI)]
    adv = advise_shot(shot, infos, "gemini_video")
    assert any(r.startswith("固定时长引擎会浪费 7 秒") for r in adv.reasons)
    shot.beats = BEATS3
    assert not any("浪费" in r for r in advise_shot(shot, infos, "gemini_video").reasons)


# ------------------------------------------------------------ 接口
def _seed_api(api, engine="gemini_video", durs=(3.0, 4.0, 3.0), blocks=(0, 0, 1)):
    api.post("/api/projects", json={"title": "demo", "negative": "", "image_engine": "zimage",
                                    "video_engine": engine})
    ep = api.post("/api/projects/demo/episodes", json={"source_text": "x"}).json()
    db = api.app.state.services.db
    with db.session() as s:
        e = s.get(Episode, ep["id"])
        a = Character(project_id=e.project_id, name="林夏", visual_prompt="a young woman")
        b = Character(project_id=e.project_id, name="老周", visual_prompt="a stocky man")
        s.add_all([a, b])
        bl = [SceneBlock(episode_id=ep["id"], order=i, text="t") for i in range(2)]
        s.add_all(bl)
        s.flush()
        casts = [[a.id], [a.id, b.id], [b.id]]
        for i, d in enumerate(durs):
            s.add(Shot(episode_id=ep["id"], order=i, scene_block_id=bl[blocks[i]].id, duration=d,
                       shot_size=["中景", "特写", "全景"][i], camera_move="static", action=f"动作{i}",
                       motion_prompt=f"motion {i}. No background music." if i else f"motion {i}",
                       image_prompt=f"prompt {i}", props=[f"prop{i}", "cup"],
                       cast=[{"character_id": c, "variation_id": None} for c in casts[i]],
                       dialogue=[{"speaker": "林夏", "zh": f"第{i}句", "start": 0.5, "end": 2.0}],
                       transition_out="dissolve_0.5" if i == 1 else "hard_cut"))
        s.flush()
        renumber_shots(s, ep["id"])
        ids = [x.id for x in sorted(s.get(Episode, ep["id"]).shots, key=lambda x: x.order)]
        # 下一镜有一条视频候选：合并后进「历史候选」
        slot = get_or_create_slot(s, "shot", ids[1], "video")
        add_candidate(s, slot, file_path="v.mp4", engine="gemini_video", params={}, input_hash="h",
                      job_id=None)
    return ep["id"], ids, (a.id, b.id)


def test_merge_next_as_beats(api):
    ep_id, ids, (a, b) = _seed_api(api)
    r = api.post(f"/api/shots/{ids[0]}/merge-next-as-beats")
    assert r.status_code == 200, r.text
    m = r.json()
    assert m["duration"] == 7.0 and m["code"] == "S01"
    assert [(x["start"], x["end"], x["shot_size"]) for x in m["beats"]] == [(0, 3.0, "中景"), (3.0, 7.0, "特写")]
    assert m["beats"][1]["action"] == "motion 1. No background music."
    assert [(d["zh"], d["start"], d["end"]) for d in m["dialogue"]] == [("第0句", 0.5, 2.0), ("第1句", 3.5, 5.0)]
    assert [c["character_id"] for c in m["cast"]] == [a, b]
    assert m["props"] == ["prop0", "cup", "prop1"] and m["image_prompt"] == "prompt 0"
    assert m["transition_out"] == "dissolve_0.5" and m["action"] == "动作0；动作1"
    shots = api.get(f"/api/episodes/{ep_id}").json()["shots"]
    assert [(x["id"], x["code"]) for x in shots] == [(ids[0], "S01"), (ids[2], "S02")]
    arch = api.get(f"/api/episodes/{ep_id}/archived-candidates").json()
    assert [(a["kind"], a["reason"]) for a in arch] == [("video", "merge_shot")]


def test_merge_next_refusals(api):
    ep_id, ids, _ = _seed_api(api, durs=(6.0, 5.0, 3.0))
    r = api.post(f"/api/shots/{ids[0]}/merge-next-as-beats")
    assert r.status_code == 400 and "超过一次生成的 10 秒" in r.json()["detail"]
    r = api.post(f"/api/shots/{ids[1]}/merge-next-as-beats")
    assert r.status_code == 400 and "另一个段落" in r.json()["detail"]
    r = api.post(f"/api/shots/{ids[2]}/merge-next-as-beats")
    assert r.status_code == 400 and "最后一镜" in r.json()["detail"]
    api.patch(f"/api/shots/{ids[0]}", json={"video_engine": "h3_video"})
    r = api.post(f"/api/shots/{ids[0]}/merge-next-as-beats")
    assert r.status_code == 400 and "不用合并" in r.json()["detail"]


def test_patch_beats_validates_and_clears(api):
    ep_id, ids, _ = _seed_api(api, durs=(10.0, 4.0, 3.0))
    r = api.patch(f"/api/shots/{ids[0]}", json={"beats": BEATS3})
    assert r.status_code == 200 and len(r.json()["beats"]) == 3
    r = api.patch(f"/api/shots/{ids[0]}", json={"beats": BEATS3[:2]})
    assert r.status_code == 400 and "最后一拍" in r.json()["detail"]
    # 只改时长：拍按比例跟着缩放
    r = api.patch(f"/api/shots/{ids[0]}", json={"duration": 9})
    assert r.json()["beats"][-1]["end"] == 9
    r = api.patch(f"/api/shots/{ids[0]}", json={"duration": 3})
    assert r.status_code == 400 and "放不下" in r.json()["detail"]
    assert api.patch(f"/api/shots/{ids[0]}", json={"beats": None}).json()["beats"] is None


def test_split_beats_shot_at_beat_boundary(api):
    ep_id, ids, _ = _seed_api(api, durs=(10.0, 4.0, 3.0))
    api.patch(f"/api/shots/{ids[0]}", json={"beats": BEATS3})
    a, b = api.post(f"/api/shots/{ids[0]}/split").json()
    assert a["duration"] == 3.5 and a["beats"] is None             # 剩一拍：普通镜头
    assert b["duration"] == 6.5 and [x["start"] for x in b["beats"]] == [0, 3.0]


def test_project_fixed_engine_pack_option(api):
    api.post("/api/projects", json={"title": "demo", "negative": ""})
    assert api.get("/api/projects/demo").json()["fixed_engine_pack"] == "beats"
    assert api.patch("/api/projects/demo", json={"fixed_engine_pack": "off"}).json()["fixed_engine_pack"] == "off"
    assert api.patch("/api/projects/demo", json={"fixed_engine_pack": "group"}).status_code == 200
    assert api.patch("/api/projects/demo", json={"fixed_engine_pack": "bogus"}).status_code == 422


def test_engines_api_exposes_ref_mode_for_web_engines(api):
    rows = {r["name"]: r for r in api.get("/api/engines").json()["video"]}
    assert rows["gemini_video"]["caps"]["ref_mode"] and rows["gemini_video"]["caps"]["max_refs"] == 3
    assert rows["muse_video"]["caps"]["duration_control"] == "prompt"
