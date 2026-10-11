import pytest
from sqlalchemy import select

from backend.domain.models import Character, Episode, Location, Project, SceneBlock, Shot, StyleBible
from backend.llm.base import LLMError
from backend.pipeline.storyboard import allocate_shots, run_storyboard, shot_budget
from backend.queue.errors import JobError
from backend.tests.helpers import FakeCtx, make_gateway, make_services, seed_episode

LOOK = ("a woman in her late twenties with an oval face and shoulder length black hair, "
        "slim build, charcoal wool blazer over a white silk blouse, small silver ear cuff")


def shots_json(n, cast=("林夏",), duration=5):
    img = "medium close-up, 85mm, eye level. " + LOOK + " " + " ".join(["detail"] * 50)
    return {"shots": [{
        "shot_size": "近景", "camera_move": "static", "action": f"动作{i}", "cast": list(cast),
        "dialogue": ([{"speaker": "林夏", "zh": "词被改了", "start": 0.5, "end": 2.0}] if i == 0 else []),
        "image_prompt": img, "motion_prompt": "she looks up slowly", "audio_prompt": "quiet hum",
        "duration": duration, "transition_out": "hard_cut"} for i in range(n)]}


def seed_scripted(db, **kw):
    proj_id, ep_id = seed_episode(db, target=20.0, **kw)
    with db.session() as s:
        proj = s.get(Project, proj_id)
        proj.style_bible = StyleBible(anchor_text="ANCHOR photorealistic drama")
        s.add(Character(project_id=proj_id, name="林夏", visual_prompt=LOOK))
        loc = Location(project_id=proj_id, name="直播间", time_of_day="夜晚", visual_prompt="ROOM")
        s.add(loc)
        s.flush()
        ep = s.get(Episode, ep_id)
        ep.scene_blocks.append(SceneBlock(order=0, location_id=loc.id, text="甲" * 50))
        ep.scene_blocks.append(SceneBlock(order=1, location_id=loc.id, text="乙" * 50))
    return ep_id


def setup(tmp_path, db, texts, **kw):
    gw, prov = make_gateway(texts)
    ctx = FakeCtx(make_services(tmp_path, db, llm=gw))
    return ctx, seed_scripted(db, **kw), prov


def test_budget_and_allocation():
    assert shot_budget(20, 5) == 4 and shot_budget(2, 5) == 1
    assert allocate_shots(6, [1, 1, 1]) == [2, 2, 2]
    assert allocate_shots(5, [3, 1]) == [3, 2]
    assert allocate_shots(2, [1, 1, 1]) == [1, 1, 1]
    assert sum(allocate_shots(24, [120, 30, 300, 55])) == 24


def test_storyboard_creates_numbered_shots(tmp_path, db):
    ctx, ep_id, prov = setup(tmp_path, db, [shots_json(2), shots_json(2)])
    assert run_storyboard(ctx, {"episode_id": ep_id}) == {"blocks_done": 2, "blocks_skipped": 0}
    with db.session() as s:
        shots = s.get(Episode, ep_id).shots
        assert [x.code for x in shots] == ["S01", "S02", "S03", "S04"]
        lin_id = s.scalar(select(Character.id))
        assert shots[0].cast == [{"character_id": lin_id, "variation_id": None}]
        assert shots[0].dialogue[0]["zh"] == "词被改了"
        blocks = s.get(Episode, ep_id).scene_blocks
        assert [x.scene_block_id for x in shots] == [blocks[0].id] * 2 + [blocks[1].id] * 2
        assert s.get(Episode, ep_id).status == "storyboarded"
    user = prov.calls[0]["messages"][0]["content"]
    assert "正好 2 个镜头" in user and LOOK in user and "ROOM" in user and "ANCHOR" in user
    assert "上一镜结束时：动作1" in prov.calls[1]["messages"][0]["content"]


def test_skip_unchanged(tmp_path, db):
    ctx, ep_id, prov = setup(tmp_path, db, [shots_json(2), shots_json(2)])
    run_storyboard(ctx, {"episode_id": ep_id})
    assert run_storyboard(ctx, {"episode_id": ep_id}) == {"blocks_done": 0, "blocks_skipped": 2}
    assert len(prov.calls) == 2


def test_wrong_count_fails_and_keeps_db_clean(tmp_path, db):
    ctx, ep_id, prov = setup(tmp_path, db, [shots_json(2), shots_json(2),
                                            shots_json(3), shots_json(1), shots_json(3)])
    run_storyboard(ctx, {"episode_id": ep_id})
    with db.session() as s:
        before = [x.id for x in s.get(Episode, ep_id).shots]
    with pytest.raises(LLMError) as ei:
        run_storyboard(ctx, {"episode_id": ep_id, "force": True})
    assert "需要正好 2 个镜头" in str(ei.value) and "动作2" in str(ei.value)
    with db.session() as s:
        assert [x.id for x in s.get(Episode, ep_id).shots] == before


def test_unknown_cast_retries(tmp_path, db):
    ctx, ep_id, prov = setup(tmp_path, db, [shots_json(2, cast=("路人甲",)), shots_json(2),
                                            shots_json(2)])
    run_storyboard(ctx, {"episode_id": ep_id})
    assert "未知角色" in prov.calls[1]["messages"][2]["content"]


def test_duration_clamped_to_engine_max(tmp_path, db):
    ctx, ep_id, _ = setup(tmp_path, db, [shots_json(2, duration=8), shots_json(2, duration=8)],
                          video_engine="wan22_i2v")
    run_storyboard(ctx, {"episode_id": ep_id})
    with db.session() as s:
        assert {x.duration for x in s.get(Episode, ep_id).shots} == {5.0}


def test_duration_clamp_trims_dialogue_past_new_duration(tmp_path, db):
    img = "medium close-up, 85mm, eye level. " + LOOK + " " + " ".join(["detail"] * 50)
    block1 = {"shots": [
        {"shot_size": "近景", "camera_move": "static", "action": "动作0", "cast": ["林夏"],
         "dialogue": [{"speaker": "林夏", "zh": "第一句", "start": 0.5, "end": 6.5},
                      {"speaker": "林夏", "zh": "第二句", "start": 6.0, "end": 7.5}],
         "image_prompt": img, "motion_prompt": "she looks up slowly", "audio_prompt": "quiet hum",
         "duration": 8, "transition_out": "hard_cut"},
        {"shot_size": "近景", "camera_move": "static", "action": "动作1", "cast": ["林夏"],
         "dialogue": [], "image_prompt": img, "motion_prompt": "she looks up slowly",
         "audio_prompt": "quiet hum", "duration": 8, "transition_out": "hard_cut"},
    ]}
    ctx, ep_id, _ = setup(tmp_path, db, [block1, shots_json(2, duration=8)],
                          video_engine="wan22_i2v")
    run_storyboard(ctx, {"episode_id": ep_id})
    with db.session() as s:
        first = s.get(Episode, ep_id).shots[0]
        assert first.duration == 5.0
        assert len(first.dialogue) == 1
        assert first.dialogue[0]["end"] == 5.0
    assert any("裁掉 1 句" in m for m in ctx.logs)


def test_regenerate_single_block_keeps_others(tmp_path, db):
    ctx, ep_id, prov = setup(tmp_path, db, [shots_json(2), shots_json(2), shots_json(2)])
    run_storyboard(ctx, {"episode_id": ep_id})
    with db.session() as s:
        ep = s.get(Episode, ep_id)
        b2 = ep.scene_blocks[1].id
        first_two = [x.id for x in ep.shots[:2]]
    run_storyboard(ctx, {"episode_id": ep_id, "scene_block_ids": [b2], "force": True})
    with db.session() as s:
        shots = s.get(Episode, ep_id).shots
        assert [x.id for x in shots[:2]] == first_two
        assert [x.code for x in shots] == ["S01", "S02", "S03", "S04"]
    assert len(prov.calls) == 3


def test_no_blocks_raises(tmp_path, db):
    gw, _ = make_gateway([])
    ctx = FakeCtx(make_services(tmp_path, db, llm=gw))
    _, ep_id = seed_episode(db)
    with pytest.raises(JobError, match="script"):
        run_storyboard(ctx, {"episode_id": ep_id})


def test_restoryboard_drops_slots_of_removed_shots(tmp_path, db):
    from sqlalchemy import func

    from backend.domain.models import Candidate, Slot
    from backend.domain.slots import add_candidate, get_or_create_slot, slots_for

    ctx, ep_id, _ = setup(tmp_path, db, [shots_json(2), shots_json(2), shots_json(2)])
    run_storyboard(ctx, {"episode_id": ep_id})
    with db.session() as s:
        ep = s.get(Episode, ep_id)
        b2 = ep.scene_blocks[1].id
        old = [x.id for x in ep.shots[2:]]
        for sid in old:
            add_candidate(s, get_or_create_slot(s, "shot", sid, "video"),
                          file_path=f"demo/media/video/{sid}.mp4", engine="x", params={},
                          input_hash="h", job_id=None)
    run_storyboard(ctx, {"episode_id": ep_id, "scene_block_ids": [b2], "force": True})
    with db.session() as s:
        ids = [x.id for x in s.get(Episode, ep_id).shots]
        assert not set(old) & set(ids)  # AUTOINCREMENT：删掉的 id 不再复用
        assert all(slots_for(s, "shot", i) == [] for i in ids)
        assert s.scalar(select(func.count()).select_from(Slot)) == 0
        assert s.scalar(select(func.count()).select_from(Candidate)) == 0


def test_manual_shot_survives_full_rerun(tmp_path, db):
    """手工加的镜头没有 scene_block_id，分镜孤儿清理不能把它当成废弹误删。"""
    ctx, ep_id, _ = setup(tmp_path, db, [shots_json(2), shots_json(2),
                                         shots_json(2), shots_json(2)])
    run_storyboard(ctx, {"episode_id": ep_id})
    with db.session() as s:
        manual = Shot(episode_id=ep_id, order=999, action="手工加的", origin="manual")
        s.add(manual)
        s.flush()
        manual_id = manual.id
    run_storyboard(ctx, {"episode_id": ep_id, "force": True})
    with db.session() as s:
        shots = s.get(Episode, ep_id).shots
        ids = [x.id for x in shots]
        assert manual_id in ids
        kept = next(x for x in shots if x.id == manual_id)
        assert kept.action == "手工加的" and kept.scene_block_id is None
        assert sum(1 for x in shots if x.origin == "llm") == 4


def test_block_with_only_manual_shots_is_not_skipped(tmp_path, db):
    """段落里只剩手工镜头（llm 镜头都删了）时，指纹没变也要重跑，不能当成「已有镜头」跳过。"""
    ctx, ep_id, prov = setup(tmp_path, db, [shots_json(2), shots_json(2), shots_json(2)])
    run_storyboard(ctx, {"episode_id": ep_id})
    with db.session() as s:
        b1 = s.get(Episode, ep_id).scene_blocks[0].id
        for sh in s.scalars(select(Shot).where(Shot.scene_block_id == b1)):
            s.delete(sh)
        # 动作和原来段末镜头一样，第二段的「上一镜结束时」不变，只看第一段
        s.add(Shot(episode_id=ep_id, scene_block_id=b1, order=5, action="动作1", origin="manual"))
    assert run_storyboard(ctx, {"episode_id": ep_id}) == {"blocks_done": 1, "blocks_skipped": 1}
    assert len(prov.calls) == 3


def test_note_forces_every_block_and_goes_into_prompt(tmp_path, db):
    ctx, ep_id, prov = setup(tmp_path, db, [shots_json(2)] * 4)
    run_storyboard(ctx, {"episode_id": ep_id})
    assert "修改意见" not in prov.calls[0]["messages"][0]["content"]
    assert "上一版本段镜头" not in prov.calls[0]["messages"][0]["content"]
    assert run_storyboard(ctx, {"episode_id": ep_id, "note": "更紧凑"}) == {"blocks_done": 2,
                                                                         "blocks_skipped": 0}
    for call in prov.calls[2:]:
        user = call["messages"][0]["content"]
        assert "修改意见：更紧凑" in user
        # 上一版本段的镜头（动作｜台词）附在提示词里
        assert "上一版本段镜头" in user
        assert "动作0｜林夏：词被改了" in user and "动作1｜无台词" in user


def test_after_note_regen_unforced_run_with_same_inputs_skips(tmp_path, db):
    """带修改意见重出过（指纹只记不含意见的基础部分）：之后不强制、输入没变的整集运行要跳过，
    不能因为「指纹里有意见」把按意见改好的分镜又按原样重做一遍。"""
    ctx, ep_id, prov = setup(tmp_path, db, [shots_json(2)] * 4)
    run_storyboard(ctx, {"episode_id": ep_id})
    with db.session() as s:
        before = dict(s.get(Episode, ep_id).stage_hashes)
    run_storyboard(ctx, {"episode_id": ep_id, "note": "更紧凑"})
    with db.session() as s:
        assert s.get(Episode, ep_id).stage_hashes == before
    assert run_storyboard(ctx, {"episode_id": ep_id}) == {"blocks_done": 0, "blocks_skipped": 2}
    assert len(prov.calls) == 4


def test_scene_block_ids_only_replaces_that_block(tmp_path, db):
    ctx, ep_id, prov = setup(tmp_path, db, [shots_json(2)] * 3)
    run_storyboard(ctx, {"episode_id": ep_id})
    with db.session() as s:
        ep = s.get(Episode, ep_id)
        b1, b2 = ep.scene_blocks[0].id, ep.scene_blocks[1].id
        b1_ids = sorted(x.id for x in ep.shots if x.scene_block_id == b1)
        b2_ids = sorted(x.id for x in ep.shots if x.scene_block_id == b2)
        manual = Shot(episode_id=ep_id, scene_block_id=b2, order=1999, action="手工",
                      origin="manual")
        s.add(manual)
        s.flush()
        manual_id = manual.id
    out = run_storyboard(ctx, {"episode_id": ep_id, "scene_block_ids": [b2], "note": "换个机位"})
    assert out == {"blocks_done": 1, "blocks_skipped": 0}
    assert len(prov.calls) == 3
    user = prov.calls[2]["messages"][0]["content"]
    assert "乙" * 50 in user and "修改意见：换个机位" in user
    with db.session() as s:
        ep = s.get(Episode, ep_id)
        assert sorted(x.id for x in ep.shots if x.scene_block_id == b1) == b1_ids
        new_b2 = [x.id for x in ep.shots if x.scene_block_id == b2 and x.origin == "llm"]
        assert len(new_b2) == 2 and not set(new_b2) & set(b2_ids)
        assert manual_id in [x.id for x in ep.shots]


# ---------------------------------------------------------------- 质量规则：中文字符、时长预算
def test_cjk_detection():
    from backend.pipeline.storyboard import cjk_chars

    assert cjk_chars("老周 at the shutter") == "老周"
    assert cjk_chars("A grizzled repairman，rain") == "，"
    assert cjk_chars("ナイト and 한국") == "ナイト한국"
    assert cjk_chars("A 35mm close-up — café, naïve “quotes”") == ""


def test_cjk_in_prompts_retries_with_error(tmp_path, db):
    bad = shots_json(2)
    bad["shots"][1]["image_prompt"] = "林夏 " + bad["shots"][1]["image_prompt"]
    bad["shots"][0]["motion_prompt"] = "林夏 looks up slowly"
    ctx, ep_id, prov = setup(tmp_path, db, [bad, shots_json(2), shots_json(2)])
    run_storyboard(ctx, {"episode_id": ep_id})
    err = prov.calls[1]["messages"][2]["content"]
    assert "中文" in err and "林夏" in err and "外貌" in err
    assert "第 2 镜 image_prompt" in err and "第 1 镜 motion_prompt" in err
    with db.session() as s:
        assert all("林夏" not in x.image_prompt for x in s.get(Episode, ep_id).shots)


def test_fit_durations_scales_into_budget_within_limits():
    from backend.pipeline.storyboard import fit_durations

    # 实跑：目标 25 秒，给了 6+7+5+5+8=31 秒 → 按比例缩到 25 秒左右
    out = fit_durations([6, 7, 5, 5, 8], [2] * 5, 25.0, hi=10.0)
    assert abs(sum(out) - 25.0) <= 0.3 and all(2 <= d <= 10 for d in out)
    assert out[4] > out[2]  # 长短关系保留
    # 有台词的镜头不能缩到台词说完之前：下限顶住，其他镜头多缩一点
    out = fit_durations([6, 6], [5.5, 2], 8.0, hi=10.0)
    assert out[0] == 5.5 and abs(sum(out) - 8.0) <= 0.1
    # 加长时不超过引擎上限
    out = fit_durations([3, 3], [2, 2], 14.0, hi=5.0)
    assert out == [5.0, 5.0]


def test_block_target_share():
    from backend.pipeline.storyboard import block_seconds

    assert block_seconds(25.0, 3, 5) == 15.0 and block_seconds(25.0, 2, 5) == 10.0


def test_storyboard_rescales_block_to_target_and_logs(tmp_path, db):
    # target 20 秒、两段各 2 镜 → 每段 10 秒；LLM 给了 7+7=14 秒
    ctx, ep_id, prov = setup(tmp_path, db, [shots_json(2, duration=7), shots_json(2)])
    run_storyboard(ctx, {"episode_id": ep_id})
    with db.session() as s:
        shots = s.get(Episode, ep_id).shots
        assert abs(sum(x.duration for x in shots) - 20.0) <= 2.0
        assert [x.duration for x in shots[:2]] == [5.0, 5.0]
    assert any("14.0" in m and "10.0" in m for m in ctx.logs)
    assert "10 秒" in prov.calls[0]["messages"][0]["content"]


def test_storyboard_duration_unfittable_because_of_dialogue_retries(tmp_path, db):
    # 每镜台词说到 6.5 秒，两镜最少 2×6.9 秒，本段只给 10 秒（±10%）：缩不下，要 LLM 重写
    long = shots_json(2, duration=7)
    for sh in long["shots"]:
        sh["dialogue"] = [{"speaker": "林夏", "zh": "词被改了", "start": 0.5, "end": 6.5}]
    ctx, ep_id, prov = setup(tmp_path, db, [long, shots_json(2), shots_json(2)])
    run_storyboard(ctx, {"episode_id": ep_id})
    err = prov.calls[1]["messages"][2]["content"]
    assert "总时长" in err and "10" in err


def test_storyboard_prompt_has_quality_rules():
    from backend.llm.prompts import PromptLibrary
    from backend.services import BUILTIN_DIR

    system, user = PromptLibrary(BUILTIN_DIR, BUILTIN_DIR / "none").render(
        "storyboard", anchor="A", characters=[], location=None, text="x", count=2,
        shot_seconds=5, max_seconds=10, prev_action="", block_seconds=10.0)
    assert "中文字符" in system and "一个主动作" in system
    assert "特写插入镜头" in system and "逐字重复" in system
    assert "10.0 秒" in user or "10 秒" in user
