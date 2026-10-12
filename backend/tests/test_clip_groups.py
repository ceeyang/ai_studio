"""固定时长视频引擎第 2 步：组镜拆分（设计文档 docs/specs/2026-10-12-fixed-length-engines-design.md §3.3）。
分组算法、分镜自动分组、合成 / 拆出组镜接口、只有主镜出视频、成员显示主镜视频和过期、重拍成员 = 重出整组、
导出按偏移截取（对齐剪切点）、字幕时间轴、抽卡统计只算一次。"""
from dataclasses import replace
from types import SimpleNamespace

import pytest
from sqlalchemy import select

import backend.pipeline  # noqa: F401  注册全部处理器
from backend.app.views import episode_detail, shot_detail
from backend.domain.clip_groups import (
    GroupShot,
    assign_groups,
    episode_groups,
    group_segments,
    merged_dialogue,
    normalize_groups,
    snap_boundaries,
)
from backend.domain.engine_advice import EngineInfo, advise_shot
from backend.domain.models import Episode, Job, Project, Shot
from backend.domain.slots import add_candidate, get_or_create_slot, selected_candidate
from backend.domain.take_stats import episode_take_stats
from backend.engines.browser.engines import GeminiVideoEngine
from backend.engines.ffmpeg import build_normalize_cmd
from backend.pipeline.export import run_export
from backend.pipeline.post import clip_limits, load_cut
from backend.pipeline.runs import Precheck, RunOptions, submit_chain
from backend.pipeline.storyboard import run_storyboard
from backend.pipeline.videos import run_video
from backend.queue.handlers import HANDLERS
from backend.queue.manager import JobManager
from backend.tests.helpers import FakeCtx, FakeFFmpeg, make_gateway, make_services
from backend.tests.test_storyboard import seed_scripted, shots_json
from backend.tests.test_video_refs_mode import _portrait, setup

GEMINI = GeminiVideoEngine.caps


def _s(i, dur, block=1, group=None, order=None):
    return SimpleNamespace(id=i, order=i if order is None else order, code=f"S{i:02d}", duration=dur,
                           scene_block_id=block, clip_group=group)


# ------------------------------------------------------------ 纯函数
def test_assign_groups_fills_unit_and_avoids_singletons():
    assert assign_groups([4, 3, 3, 4, 4], 10) == [0, 0, 0, 1, 1]
    assert assign_groups([6, 6], 10) == [None, None]           # 两镜放不进一次生成：各自出
    assert assign_groups([3], 10) == [None]
    # 生成次数一样多时少留单独出的镜头：[5,5] [4,4] 好过 [5,5] [4] [4]…（这里也是两次）
    assert assign_groups([5, 5, 4, 4], 10) == [0, 0, 1, 1]
    # 8 秒的放不下别的：单独出，其余两两一组
    assert assign_groups([3, 3, 8, 4, 5], 10) == [0, 0, None, 1, 1]
    assert assign_groups([], 10) == []


def test_runs_and_normalize():
    shots = [_s(1, 3, group=7), _s(2, 3, group=7), _s(3, 3), _s(4, 3, group=7), _s(5, 3, group=7),
             _s(6, 3, group=9), _s(7, 3, block=2, group=9)]
    groups = episode_groups(shots)
    assert groups[1].ids == [1, 2] and groups[4].ids == [4, 5]
    assert 6 not in groups and 7 not in groups                   # 换了段落就不连着
    assert normalize_groups(shots)
    assert [x.clip_group for x in shots] == [7, 7, None, 10, 10, None, None]
    g = episode_groups(shots)[2]
    assert g.lead.id == 1 and g.index(2) == 2 and g.offsets == [0.0, 3.0] and g.total == 6.0


def test_group_shot_proxy_merges_members():
    a = SimpleNamespace(id=1, code="S01", duration=3.0, beats=None, shot_size="中景", camera_move="static",
                        motion_prompt="she sits", action="坐下", audio_prompt="rain. No background music.",
                        cast=[{"character_id": 1}], props=["cup"], image_prompt="IMG",
                        dialogue=[{"speaker": "林夏", "zh": "一", "start": 0.5, "end": 2.0}],
                        order=0, scene_block_id=1, clip_group=1, video_mode=None)
    b = SimpleNamespace(id=2, code="S02", duration=4.0, beats=None, shot_size="特写", camera_move="push in",
                        motion_prompt="", action="抬头", audio_prompt="clock. No background music.",
                        cast=[{"character_id": 1}, {"character_id": 2}], props=["cup", "key"],
                        image_prompt="IMG2", dialogue=[{"speaker": "老周", "zh": "二", "start": 1.0, "end": 2.5}],
                        order=1, scene_block_id=1, clip_group=1, video_mode=None)
    g = episode_groups([a, b])[1]
    p = GroupShot(g)
    assert p.duration == 7.0 and p.id == 1 and p.image_prompt == "IMG"
    assert [(x["start"], x["end"], x["shot_size"], x["action"]) for x in p.beats] == \
        [(0, 3.0, "中景", "she sits"), (3.0, 7.0, "特写", "抬头")]
    assert [(d["zh"], d["start"]) for d in p.dialogue] == [("一", 0.5), ("二", 4.0)]
    assert [c["character_id"] for c in p.cast] == [1, 2] and p.props == ["cup", "key"]
    assert p.audio_prompt == "rain. clock. No background music."
    assert group_segments([a, b]) == p.beats and merged_dialogue([a]) == a.dialogue


def test_snap_boundaries():
    assert snap_boundaries([3.0, 7.0], [3.4, 6.2, 8.9]) == [3.4, 7.0]   # 6.2 离 7 太远
    assert snap_boundaries([3.0], [2.5, 3.2]) == [3.2]                   # 取最近的
    assert snap_boundaries([3.0], []) == [3.0]
    assert snap_boundaries([0.4], [0.1]) == [0.4]                       # 对齐后太短：按计划


# ------------------------------------------------------------ 分镜
def test_storyboard_group_mode_assigns_groups(tmp_path, db):
    gw, prov = make_gateway([shots_json(2, duration=4), shots_json(2, duration=4)])
    ctx = FakeCtx(make_services(tmp_path, db, llm=gw))
    ep_id = seed_scripted(db, video_engine="gemini_video")
    with db.session() as s:
        ep = s.get(Episode, ep_id)
        ep.target_seconds = 16.0
        s.get(Project, ep.project_id).fixed_engine_pack = "group"
    run_storyboard(ctx, {"episode_id": ep_id})
    assert "每次固定出" not in prov.calls[0]["system"]            # 组镜模式不写多拍
    with db.session() as s:
        ep = s.get(Episode, ep_id)
        shots = sorted(ep.shots, key=lambda x: x.order)
        assert [x.duration for x in shots] == [4.0] * 4 and all(x.beats is None for x in shots)
        g = [x.clip_group for x in shots]
        assert g[0] == g[1] and g[2] == g[3] and g[0] != g[2] and None not in g
        assert {r["plan"]["mode"] for r in ep.stage_hashes.values()} == {"group"}
    assert any("排成 1 组一次生成" in m for m in ctx.logs)


# ------------------------------------------------------------ 生成
def _gsetup(tmp_path, db, n=3, durs=(3.0, 4.0, 3.0)):
    ctx, runner, eng, ids = setup(tmp_path, db, n=n)
    eng.caps = replace(eng.caps, duration_control="fixed", default_seconds=10.0, max_seconds=10.0)
    _portrait(ctx.services, ids["chen"], 3)
    with db.session() as s:
        for i, sid in enumerate(ids["shots"]):
            sh = s.get(Shot, sid)
            sh.duration = durs[i]
            sh.clip_group = 1
            sh.shot_size = ["中景", "特写", "全景"][i]
            sh.motion_prompt = f"motion {i}"
            sh.dialogue = [{"speaker": "陈师傅", "zh": f"第{i}句", "start": 0.5, "end": 2.0}]
    return ctx, runner, eng, ids


def _detail(sv, shot_id):
    with sv.db.session() as s:
        shot = s.get(Shot, shot_id)
        proj = s.get(Project, s.get(Episode, shot.episode_id).project_id)
        return shot_detail(s, sv, shot, proj, proj.style_bible)


def _vslot(d):
    return next(x for x in d.slots if x.kind == "video")


def test_member_video_job_redirects_to_lead_with_timestamped_prompt(tmp_path, db):
    ctx, _runner, eng, ids = _gsetup(tmp_path, db)
    a, b, c = ids["shots"]
    run_video(ctx, {"shot_id": b})                       # 重拍成员 = 重出整组
    assert any("改为重出整组" in m for m in ctx.logs)
    req = eng.requests[-1]
    assert req.seconds == 10.0                           # 整组总时长
    lines = req.prompt.split("\n")
    assert lines[0].startswith("[00:00-00:03] Medium shot: motion 0. Same people:")
    assert lines[1].startswith("[00:03-00:07] Close-up: motion 1.") and "第1句" in lines[1]
    assert lines[2].startswith("[00:07-00:10] Wide shot")
    with db.session() as s:
        v = selected_candidate(s, "shot", a, "video")
        assert v is not None and selected_candidate(s, "shot", b, "video") is None
        assert v.params["clip_group"] == {"members": [a, b, c], "codes": ["S01", "S02", "S03"],
                                          "offsets": [0.0, 3.0, 7.0], "durations": [3.0, 4.0, 3.0]}
    # 成员显示主镜的视频槽位，按整组算过期
    sv = ctx.services
    lead, mem = _detail(sv, a), _detail(sv, b)
    assert _vslot(mem).id == _vslot(lead).id and not _vslot(mem).stale
    assert mem.group.index == 2 and mem.group.lead_code == "S01" and mem.group.offset == 3.0
    assert mem.group.video_ready and mem.group.total == 10.0 and mem.group.unit == 10.0
    # 不强制：输入没变跳过
    assert run_video(ctx, {"shot_id": c})["skipped"]
    # 改成员的时长 → 整组过期（成员、主镜一起）
    with db.session() as s:
        s.get(Shot, c).duration = 2.5
    assert _vslot(_detail(sv, a)).stale and _vslot(_detail(sv, c)).stale
    assert not run_video(ctx, {"shot_id": c}).get("skipped")


def test_qc_cuts_on_member_boundaries_expected(tmp_path, db):
    ctx, _, _, ids = _gsetup(tmp_path, db)
    ctx.services.ffmpeg = SimpleNamespace(scene_cuts=lambda p: [3.3, 5.0, 6.6], ffmpeg="ffmpeg")
    run_video(ctx, {"shot_id": ids["shot"]})
    with db.session() as s:
        qc = selected_candidate(s, "shot", ids["shot"], "video").params["qc"]
    assert qc["beat_cuts"] == [3.3, 6.6] and qc["hard_cuts"] == [5.0]


@pytest.fixture
def manager(db):
    return JobManager(db, HANDLERS, poll=0.02)


def _jobs(db):
    with db.session() as s:
        return [(j.type, j.payload.get("shot_id"), list(j.depends_on))
                for j in s.scalars(select(Job).order_by(Job.id))]


def test_chain_only_lead_gets_video_job(tmp_path, db, manager):
    ctx, _, _, ids = _gsetup(tmp_path, db, n=3)
    a, _b, _c = ids["shots"]
    with db.session() as s:
        for sid in ids["shots"]:
            s.get(Shot, sid).video_mode = None   # 首帧模式：只有主镜要首帧
    submit_chain(manager, db, ctx.services.engines, ids["episode"], ["keyframes", "videos"], RunOptions())
    rows = _jobs(db)
    kf = [i + 1 for i, r in enumerate(rows) if r[0] == "image.keyframe"]
    assert [r[1] for r in rows if r[0] == "image.keyframe"] == [a]       # 成员首帧可选：整集不出
    assert [r for r in rows if r[0] == "video.shot"] == [("video.shot", a, kf)]


def test_chain_member_rerun_submits_lead_and_precheck(tmp_path, db, manager):
    ctx, _, _, ids = _gsetup(tmp_path, db, n=3)
    a, _b, _c = ids["shots"]
    submit_chain(manager, db, ctx.services.engines, ids["episode"], ["videos"],
                 RunOptions(shots=["S02", "S03"], force=True), precheck=Precheck())
    assert _jobs(db) == [("video.shot", a, [])]
    # 导出前检查：成员的视频看主镜
    from backend.queue.errors import JobError
    with pytest.raises(JobError, match="S01, S02, S03"):
        submit_chain(manager, db, ctx.services.engines, ids["episode"], ["export"], RunOptions(),
                     precheck=Precheck())
    with db.session() as s:
        add_candidate(s, get_or_create_slot(s, "shot", a, "video"), file_path="v.mp4", engine="x",
                      params={}, input_hash="h", job_id=None)
    assert len(submit_chain(manager, db, ctx.services.engines, ids["episode"], ["export"], RunOptions(),
                            precheck=Precheck())) == 1


def test_take_stats_count_group_once(tmp_path, db):
    ctx, _, _, ids = _gsetup(tmp_path, db)
    run_video(ctx, {"shot_id": ids["shots"][1]})
    with db.session() as s:
        st = episode_take_stats(s, ids["episode"])
    assert st.video_takes == 1 and [x.video.takes for x in st.shots] == [1, 0, 0]


def test_engine_advice_uses_group_total():
    shot = SimpleNamespace(id=1, code="S01", cast=[], action="", motion_prompt="", camera_move="",
                           dialogue=[], props=[], duration=3.0, use_end_frame=False, beats=None)
    infos = [EngineInfo("gemini_video", "Gemini 网页（Veo）", "browser", GEMINI)]
    adv = advise_shot(shot, infos, "gemini_video", group_total=6.0, pack="group")
    assert any("浪费 4 秒" in r and "这一组只用 6 秒" in r and "合成组镜" in r for r in adv.reasons)
    assert not any("浪费" in r for r in advise_shot(shot, infos, "gemini_video", group_total=9.5).reasons)


# ------------------------------------------------------------ 接口
def _seed_api(api, durs=(3.0, 4.0, 3.0, 2.0), blocks=(0, 0, 0, 1), engine="gemini_video"):
    from backend.domain.models import SceneBlock
    from backend.domain.shots import renumber_shots

    api.post("/api/projects", json={"title": "demo", "negative": "", "image_engine": "zimage",
                                    "video_engine": engine})
    ep = api.post("/api/projects/demo/episodes", json={"source_text": "x"}).json()
    db = api.app.state.services.db
    with db.session() as s:
        bl = [SceneBlock(episode_id=ep["id"], order=i, text="t") for i in range(2)]
        s.add_all(bl)
        s.flush()
        for i, d in enumerate(durs):
            s.add(Shot(episode_id=ep["id"], order=i, scene_block_id=bl[blocks[i]].id, duration=d,
                       continue_from_prev=i == 1))
        s.flush()
        ids = [x.id for x in renumber_shots(s, ep["id"])]
    return ep["id"], ids


def test_group_with_next_and_ungroup(api):
    ep_id, ids = _seed_api(api)
    r = api.post(f"/api/shots/{ids[0]}/group-with-next")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["group"]["codes"] == ["S01", "S02"] and d["group"]["index"] == 1 and d["clip_group"]
    shots = api.get(f"/api/episodes/{ep_id}").json()["shots"]
    assert shots[1]["continue_from_prev"] is False                   # 成员不接上一镜
    assert shots[1]["group"]["lead_id"] == ids[0] and shots[1]["group"]["offset"] == 3.0
    # 从成员上点也是往组的末尾加
    r = api.post(f"/api/shots/{ids[1]}/group-with-next")
    assert r.json()["group"]["codes"] == ["S01", "S02", "S03"]
    # 成员不能开接上一镜
    r = api.patch(f"/api/shots/{ids[2]}", json={"continue_from_prev": True})
    assert r.status_code == 400 and "组镜" in r.json()["detail"]
    # 拆出中间一镜：前后各剩一镜，整组解散
    r = api.post(f"/api/shots/{ids[1]}/ungroup")
    assert r.status_code == 200 and r.json()["group"] is None
    shots = api.get(f"/api/episodes/{ep_id}").json()["shots"]
    assert [x["clip_group"] for x in shots] == [None] * 4
    r = api.post(f"/api/shots/{ids[1]}/ungroup")
    assert r.status_code == 400 and "不在组镜里" in r.json()["detail"]


def test_group_with_next_refusals(api):
    _ep_id, ids = _seed_api(api, durs=(6.0, 5.0, 3.0, 2.0))
    r = api.post(f"/api/shots/{ids[0]}/group-with-next")
    assert r.status_code == 400 and "超过" in r.json()["detail"] and "10 秒" in r.json()["detail"]
    r = api.post(f"/api/shots/{ids[2]}/group-with-next")
    assert r.status_code == 400 and "另一个段落" in r.json()["detail"]
    r = api.post(f"/api/shots/{ids[3]}/group-with-next")
    assert r.status_code == 400 and "最后一镜" in r.json()["detail"]
    api.post(f"/api/shots/{ids[1]}/group-with-next")                  # S02 + S03
    r = api.post(f"/api/shots/{ids[0]}/group-with-next")
    assert r.status_code == 400 and "另一个组镜" in r.json()["detail"]
    api.patch(f"/api/shots/{ids[0]}", json={"video_engine": "h3_video"})
    api.post(f"/api/shots/{ids[1]}/ungroup")
    r = api.post(f"/api/shots/{ids[0]}/group-with-next")
    assert r.status_code == 400 and "不用合成组镜" in r.json()["detail"]


def test_split_and_delete_keep_groups_consistent(api):
    ep_id, ids = _seed_api(api)
    api.post(f"/api/shots/{ids[0]}/group-with-next")
    _a, _b = api.post(f"/api/shots/{ids[1]}/split").json()             # 拆出来的后半段留在组里
    shots = api.get(f"/api/episodes/{ep_id}").json()["shots"]
    assert shots[0]["group"]["codes"] == ["S01", "S02", "S03"]
    api.delete(f"/api/shots/{ids[0]}")                                # 删主镜：下一镜当主镜
    shots = api.get(f"/api/episodes/{ep_id}").json()["shots"]
    assert shots[0]["group"]["index"] == 1 and shots[0]["group"]["size"] == 2


# ------------------------------------------------------------ 导出
def _export_seed(tmp_path, db, qc=None, durs=(3.0, 4.0, 3.0), cont_after=False):
    sv = make_services(tmp_path, db)
    ff = FakeFFmpeg()
    ff.scene_calls = 0

    def scene_cuts(path):
        ff.scene_calls += 1
        return [3.4, 6.8]

    ff.scene_cuts = scene_cuts
    ff.video_duration = lambda p: 10.0
    sv.ffmpeg = ff
    from backend.tests.helpers import seed_episode
    _, ep_id = seed_episode(db)
    ids = []
    with db.session() as s:
        for i, d in enumerate([*durs, 3.0]):
            sh = Shot(episode_id=ep_id, order=i, code=f"S{i + 1:02d}", duration=d, scene_block_id=None,
                      clip_group=1 if i < len(durs) else None, transition_out="dissolve_0.5",
                      continue_from_prev=cont_after and i == len(durs),
                      dialogue=[{"speaker": "x", "zh": f"第{i}句", "start": 0.5, "end": 1.5}])
            s.add(sh)
            s.flush()
            ids.append(sh.id)
        for sid in (ids[0], ids[-1]):
            p = sv.media.new_media_path("demo", "video", "mp4")
            p.write_bytes(b"v")
            add_candidate(s, get_or_create_slot(s, "shot", sid, "video"), file_path=sv.media.rel(p),
                          engine="gemini_video", params={"qc": qc} if qc is not None and sid == ids[0] else {},
                          input_hash="h", job_id=None)
    return sv, ff, ep_id, ids


def test_load_cut_offsets_snap_to_detected_cuts(tmp_path, db):
    sv, ff, ep_id, _ids = _export_seed(tmp_path, db)
    cut = load_cut(sv, ep_id)
    assert cut.clips[0] == cut.clips[1] == cut.clips[2] != cut.clips[3]
    assert cut.starts_at == [0.0, 3.4, 6.8, 0.0] and ff.scene_calls == 1   # 只检测一次
    assert cut.lengths == [3.4, 3.4, None, None]
    # 组里的接缝硬切；出组那一下照常转场
    assert cut.transitions == ["hard_cut", "hard_cut", "dissolve_0.5"]
    secs, limits = clip_limits(sv, cut, trim=True)
    assert secs[:3] == [10.0, 6.6, pytest.approx(3.2)]
    # 最后一镜：剩 3.2 秒、分镜 3 秒 → 裁到 3.15（不量音轨）
    assert limits[:2] == [3.4, 3.4] and limits[2] == pytest.approx(3.15)
    # 出片自检记下的剪切点直接用，不再检测
    sv, ff, ep_id, _ = _export_seed(tmp_path / "b", db_new(tmp_path / "b"),
                                    qc={"hard_cuts": [], "beat_cuts": [2.6]})
    cut = load_cut(sv, ep_id)
    assert cut.starts_at[:3] == [0.0, 2.6, 7.0] and ff.scene_calls == 0


def db_new(path):
    from backend.domain.db import Database
    path.mkdir(parents=True, exist_ok=True)
    d = Database(path / "t.db")
    d.create_all()
    return d


def test_last_member_trims_without_sound_check_and_keeps_tail_when_next_continues(tmp_path, db):
    sv, ff, ep_id, _ids = _export_seed(tmp_path, db, durs=(3.0, 4.0))
    ff.scene_cuts = lambda p: []
    cut = load_cut(sv, ep_id)
    assert cut.starts_at[:2] == [0.0, 3.0]
    _, limits = clip_limits(sv, cut, trim=True)
    assert limits[:2] == [3.0, pytest.approx(4.15)]                   # 7 秒以后的不要（不量音轨）
    assert clip_limits(sv, cut, trim=False)[1][1] is None
    sv, ff, ep_id, _ = _export_seed(tmp_path / "c", db_new(tmp_path / "c"), durs=(3.0, 4.0), cont_after=True)
    ff.scene_cuts = lambda p: []
    cut = load_cut(sv, ep_id)
    assert cut.continued[-1] and clip_limits(sv, cut, trim=True)[1][1] is None


def test_export_passes_offsets_and_subtitles_follow_members(tmp_path, db):
    sv, ff, ep_id, _ids = _export_seed(tmp_path, db)
    run_export(FakeCtx(sv), {"episode_id": ep_id, "options": {"subtitles": "zh", "aigc_label": False,
                                                               "aigc_metadata": False}})
    call = ff.calls[0]
    assert call["starts_at"] == [0.0, 3.4, 6.8, 0.0] and call["max_seconds"][:2] == [3.4, 3.4]
    # 字幕按成员各自的起点（asm.starts）：第 2 镜的台词在它自己的开头 + 0.5 秒
    from backend.engines.ffmpeg import xfade_timeline
    from backend.post.subtitles import timeline_cues
    durs = [3.4, 3.4, 3.2, 5.0]
    _, starts, _ = xfade_timeline(durs, call["transitions"], 24)
    cues = timeline_cues(load_cut(sv, ep_id).dialogue, starts, durs)
    assert cues[1].start == pytest.approx(starts[1] + 0.5) and 3.0 < starts[1] < 3.4


def test_normalize_cmd_seeks_before_input():
    cmd = build_normalize_cmd("ffmpeg", "a.mp4", "b.mp4", width=8, height=8, fps=24, duration=3.4,
                              audio="keep", start=3.4)
    assert cmd[:5] == ["ffmpeg", "-y", "-ss", "3.400", "-i"]
    assert "-ss" not in build_normalize_cmd("ffmpeg", "a", "b", width=8, height=8, fps=24, duration=1,
                                            audio="keep")


def test_episode_detail_members_reuse_lead(tmp_path, db):
    ctx, _, _, ids = _gsetup(tmp_path, db)
    run_video(ctx, {"shot_id": ids["shot"]})
    sv = ctx.services
    with db.session() as s:
        ep = s.get(Episode, ids["episode"])
        d = episode_detail(s, sv, ep)
    v = [next(x for x in sh.slots if x.kind == "video") for sh in d.shots]
    assert len({x.id for x in v}) == 1 and [sh.group.index for sh in d.shots] == [1, 2, 3]
