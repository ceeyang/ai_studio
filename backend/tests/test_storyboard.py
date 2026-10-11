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
    # 林夏是每个测试库里第一个角色（id=1）：cast 里有她就写她的外貌占位符
    who = "{{char:1}} The young woman sits still. " if "林夏" in cast else ""
    img = "medium close-up, 85mm, eye level. " + who + " ".join(["detail"] * 60)
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
    img = "medium close-up, 85mm, eye level. {{char:1}} The young woman sits. " + " ".join(["detail"] * 50)
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
        after = s.get(Episode, ep_id).stage_hashes
        assert {k: v["inputs"] for k, v in after.items()} == {k: v["inputs"] for k, v in before.items()}
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
    # target 20 秒、两段各 2 镜 → 每段 10 秒；LLM 给了 6+6=12 秒（缩 17%，在 25% 以内按比例缩）
    ctx, ep_id, prov = setup(tmp_path, db, [shots_json(2, duration=6), shots_json(2)])
    run_storyboard(ctx, {"episode_id": ep_id})
    with db.session() as s:
        shots = s.get(Episode, ep_id).shots
        assert abs(sum(x.duration for x in shots) - 20.0) <= 2.0
        assert [x.duration for x in shots[:2]] == [5.0, 5.0]
    assert any("12.0" in m and "10.0" in m for m in ctx.logs)
    assert "10 秒" in prov.calls[0]["messages"][0]["content"]


def test_storyboard_duration_unfittable_because_of_dialogue_retries(tmp_path, db):
    # 每镜台词说到 6.0 秒，两镜最少 2×6.4 秒，本段只给 10 秒（±10%）：缩不下，要 LLM 重写
    long = shots_json(2, duration=6.5)
    for sh in long["shots"]:
        sh["dialogue"] = [{"speaker": "林夏", "zh": "词被改了", "start": 0.5, "end": 6.0}]
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


# ---------------------------------------------------------------- 指纹：输入指纹 + 规则版本
def test_record_is_inputs_rules_and_shot_hashes(tmp_path, db):
    from backend.pipeline.storyboard import STORYBOARD_RULES_VERSION, edited_shot_ids

    ctx, ep_id, _ = setup(tmp_path, db, [shots_json(2)] * 2)
    run_storyboard(ctx, {"episode_id": ep_id})
    with db.session() as s:
        ep = s.get(Episode, ep_id)
        b1 = ep.scene_blocks[0].id
        rec = ep.stage_hashes[f"storyboard:{b1}"]
        assert rec["rules"] == str(STORYBOARD_RULES_VERSION) and len(rec["inputs"]) == 16
        mine = [x for x in ep.shots if x.scene_block_id == b1]
        assert sorted(rec["shots"]) == sorted(str(x.id) for x in mine)
        assert edited_shot_ids(rec, mine) == []
        mine[0].image_prompt += " with a red umbrella"
        assert edited_shot_ids(rec, mine) == [mine[0].id]


def test_rules_version_change_alone_does_not_rerun(tmp_path, db, monkeypatch):
    """改了分镜模板（规则版本加一）：不强制的运行不重跑，只提示；强制才按新规则重出。"""
    import backend.pipeline.storyboard as sb
    from backend.pipeline.impact import rerun_impact

    ctx, ep_id, prov = setup(tmp_path, db, [shots_json(2)] * 4)
    run_storyboard(ctx, {"episode_id": ep_id})
    with db.session() as s:
        ids_before = sorted(x.id for x in s.get(Episode, ep_id).shots)
    monkeypatch.setattr(sb, "STORYBOARD_RULES_VERSION", sb.STORYBOARD_RULES_VERSION + 1)
    ctx.logs.clear()
    assert run_storyboard(ctx, {"episode_id": ep_id}) == {"blocks_done": 0, "blocks_skipped": 2}
    assert len(prov.calls) == 2
    assert sum("分镜规则已更新，要按新规则重出请强制重跑" in m for m in ctx.logs) == 2
    with db.session() as s:
        assert sorted(x.id for x in s.get(Episode, ep_id).shots) == ids_before
        im = rerun_impact(s, ctx.services, ep_id, "storyboard", False)
        assert im.will_run is False and im.rules_outdated is True and im.shots_deleted == 0
    assert run_storyboard(ctx, {"episode_id": ep_id, "force": True})["blocks_done"] == 2
    with db.session() as s:
        im = rerun_impact(s, ctx.services, ep_id, "storyboard", False)
        assert im.rules_outdated is False
        recs = [v for k, v in s.get(Episode, ep_id).stage_hashes.items()
                if k.startswith("storyboard:")]
        assert all(r["rules"] == str(sb.STORYBOARD_RULES_VERSION) for r in recs)


def test_template_text_is_not_part_of_inputs_fingerprint(tmp_path, db, monkeypatch):
    """模板文字变了（比如这次的提示词修改）但没加规则版本：输入指纹不变，也不会重跑。"""
    from backend.llm.prompts import PromptLibrary

    ctx, ep_id, prov = setup(tmp_path, db, [shots_json(2)] * 2)
    run_storyboard(ctx, {"episode_id": ep_id})
    real = PromptLibrary.render

    def render(self, name, **kw):
        system, user = real(self, name, **kw)
        return system + "\n新加的一条规则", user

    monkeypatch.setattr(PromptLibrary, "render", render)
    assert run_storyboard(ctx, {"episode_id": ep_id}) == {"blocks_done": 0, "blocks_skipped": 2}


def test_inputs_change_still_reruns(tmp_path, db):
    ctx, ep_id, prov = setup(tmp_path, db, [shots_json(2)] * 3)
    run_storyboard(ctx, {"episode_id": ep_id})
    with db.session() as s:
        s.get(Episode, ep_id).scene_blocks[1].text = "丙" * 50
    assert run_storyboard(ctx, {"episode_id": ep_id}) == {"blocks_done": 1, "blocks_skipped": 1}


def test_project_override_template_counts_as_rules(tmp_path, db):
    from backend.pipeline.storyboard import STORYBOARD_RULES_VERSION, storyboard_rules

    ctx, ep_id, _ = setup(tmp_path, db, [])
    assert storyboard_rules(ctx.services.prompts("demo")) == str(STORYBOARD_RULES_VERSION)
    d = ctx.services.media.project_dir("demo") / "prompts"
    d.mkdir(parents=True, exist_ok=True)
    (d / "storyboard.md").write_text("自定义\n=== user ===\n{{ text }}", encoding="utf-8")
    r = storyboard_rules(ctx.services.prompts("demo"))
    assert r.startswith(f"{STORYBOARD_RULES_VERSION}+") and len(r) > 3


def test_upgrade_from_old_whole_prompt_hash_keeps_shots_and_selection(tmp_path, db):
    """升级场景：库里是旧格式指纹（整段提示词的哈希，模板改了没法重算）、镜头已经选了视频。
    不强制的整集运行不能删镜头、不能调 LLM；旧值换成新格式，之后照常按输入判断。"""
    from backend.domain.slots import add_candidate, get_or_create_slot
    from backend.pipeline.impact import rerun_impact

    ctx, ep_id, prov = setup(tmp_path, db, [shots_json(2)] * 3)
    run_storyboard(ctx, {"episode_id": ep_id})
    with db.session() as s:
        ep = s.get(Episode, ep_id)
        olds = ["c22408c005790620", "316c7dcffe702b1c"]
        ep.stage_hashes = {"structure": "fa8c14136157dc75",
                           **{f"storyboard:{b.id}": o for b, o in zip(ep.scene_blocks, olds)}}
        first = ep.shots[0].id
        add_candidate(s, get_or_create_slot(s, "shot", first, "video"),
                      file_path="demo/media/video/x.mp4", engine="h3_video", params={},
                      input_hash="h", job_id=None)
        ids_before = sorted(x.id for x in ep.shots)
    with db.session() as s:
        im = rerun_impact(s, ctx.services, ep_id, "storyboard", False)
        assert im.will_run is False and im.rules_outdated is True and im.selected_videos == 0
    ctx.logs.clear()
    assert run_storyboard(ctx, {"episode_id": ep_id}) == {"blocks_done": 0, "blocks_skipped": 2}
    assert len(prov.calls) == 2
    assert any("旧版记录，无法判断有没有变" in m and "重新生成" in m for m in ctx.logs)
    assert not any("输入没变" in m for m in ctx.logs)
    with db.session() as s:
        ep = s.get(Episode, ep_id)
        assert sorted(x.id for x in ep.shots) == ids_before
        recs = [ep.stage_hashes[f"storyboard:{b.id}"] for b in ep.scene_blocks]
        assert all(isinstance(r, dict) and r["rules"] is None for r in recs)
        assert ep.stage_hashes["structure"] == "fa8c14136157dc75"
        ep.scene_blocks[1].text = "丙" * 50      # 升级之后输入真变了：照常重跑这一段
    assert run_storyboard(ctx, {"episode_id": ep_id}) == {"blocks_done": 1, "blocks_skipped": 1}


def test_old_format_hash_without_llm_shots_still_runs(tmp_path, db):
    ctx, ep_id, prov = setup(tmp_path, db, [shots_json(2)] * 2)
    with db.session() as s:
        ep = s.get(Episode, ep_id)
        ep.stage_hashes = {f"storyboard:{b.id}": "c22408c005790620" for b in ep.scene_blocks}
    assert run_storyboard(ctx, {"episode_id": ep_id}) == {"blocks_done": 2, "blocks_skipped": 0}


# ---------------------------------------------------------------- 道具说法跨段沿用
def test_props_from_earlier_blocks_go_into_next_block_prompt(tmp_path, db):
    first = shots_json(2)
    scooter = "a battered red scooter with a cracked left mirror"
    first["shots"][0]["props"] = [scooter]
    first["shots"][1]["props"] = [scooter, "a yellowed envelope"]
    ctx, ep_id, prov = setup(tmp_path, db, [first, shots_json(2)])
    run_storyboard(ctx, {"episode_id": ep_id})
    u1 = prov.calls[0]["messages"][0]["content"]
    u2 = prov.calls[1]["messages"][0]["content"]
    assert "已定的道具英文说法" not in u1
    assert "已定的道具英文说法" in u2
    assert u2.count(scooter) == 1 and "a yellowed envelope" in u2
    with db.session() as s:
        assert s.get(Episode, ep_id).shots[1].props[1] == "a yellowed envelope"


def test_cjk_in_props_and_audio_rejected_but_quoted_sign_text_allowed():
    from types import SimpleNamespace as NS

    from backend.pipeline.storyboard import cjk_problems

    ok = NS(image_prompt='a shop sign reading "老周修车" above the shutter',
            motion_prompt="static", audio_prompt="rain on metal",
            props=['a tin sign reading "老周修车"'])
    assert cjk_problems([ok]) == []
    bad = NS(image_prompt="x", motion_prompt="x", audio_prompt="老周 sighs", props=["红色摩托"])
    out = cjk_problems([bad])
    assert any("audio_prompt" in x for x in out) and any("props" in x for x in out)


def test_shrink_over_25_percent_asks_llm_to_rewrite(tmp_path, db):
    # 本段预算 10 秒，LLM 给了 7+7=14 秒（要砍 29%）：打回让 LLM 重新分配，不机械缩放
    ctx, ep_id, prov = setup(tmp_path, db,
                             [shots_json(2, duration=7), shots_json(2), shots_json(2)])
    run_storyboard(ctx, {"episode_id": ep_id})
    err = prov.calls[1]["messages"][2]["content"]
    assert "14.0" in err and "10.0" in err and "重新分配" in err
    with db.session() as s:
        assert [x.duration for x in s.get(Episode, ep_id).shots[:2]] == [5.0, 5.0]


def test_shrink_over_25_percent_is_scaled_on_last_attempt(tmp_path, db):
    from backend.llm.gateway import MAX_RETRIES

    tries = [shots_json(2, duration=7)] * (1 + MAX_RETRIES) + [shots_json(2)]
    ctx, ep_id, prov = setup(tmp_path, db, tries)
    run_storyboard(ctx, {"episode_id": ep_id})
    with db.session() as s:
        assert [x.duration for x in s.get(Episode, ep_id).shots[:2]] == [5.0, 5.0]
    assert any("按比例调成" in m for m in ctx.logs)


def test_storyboard_prompt_rules_v2():
    from backend.llm.prompts import PromptLibrary
    from backend.services import BUILTIN_DIR

    system, user = PromptLibrary(BUILTIN_DIR, BUILTIN_DIR / "none").render(
        "storyboard", anchor="A", characters=[], location=None, text="x", count=3,
        shot_seconds=5, max_seconds=10, prev_action="", block_seconds=15.0,
        known_props=["a battered red scooter"])
    assert "输出前自检" in system and system.index("输出前自检") > system.index("transition_out")
    assert "不要自己抄进来" in system and "风格锚点" not in system
    assert "the stocky man" in system and "按这一镜的景别" in system
    assert "off-screen voice" in system and "lips closed" in system
    assert "这 3 个镜头之内" in system and "不能额外多出镜头" in system
    assert '"老周修车"' in system
    assert "a battered red scooter" in user and "逐字沿用" in user


def test_shrink_fallback_after_schema_failures_on_last_attempt(tmp_path, db):
    """前面几次输出连 schema 都不合格（check 没被调用），最后一次超预算 29%：仍然按比例缩放兜底，不整段失败。"""
    from backend.llm.gateway import MAX_RETRIES

    tries = ["不是 JSON"] * MAX_RETRIES + [shots_json(2, duration=7), shots_json(2)]
    ctx, ep_id, prov = setup(tmp_path, db, tries)
    run_storyboard(ctx, {"episode_id": ep_id})
    with db.session() as s:
        assert [x.duration for x in s.get(Episode, ep_id).shots[:2]] == [5.0, 5.0]


def test_shrink_fallback_with_manual_provider(tmp_path, db):
    """手工粘贴的分镜超预算 29%：没有「让模型再写」的循环，直接按比例缩放，不再要人改。"""
    import json

    gw, _ = make_gateway([], kind="manual")
    ctx = FakeCtx(make_services(tmp_path, db, llm=gw))
    ep_id = seed_scripted(db)
    with db.session() as s:
        keys = [f"storyboard:{b.id}" for b in s.get(Episode, ep_id).scene_blocks]
    manual = {keys[0]: json.dumps(shots_json(2, duration=7), ensure_ascii=False),
              keys[1]: json.dumps(shots_json(2), ensure_ascii=False)}
    run_storyboard(ctx, {"episode_id": ep_id, "manual": manual})
    with db.session() as s:
        assert [x.duration for x in s.get(Episode, ep_id).shots[:2]] == [5.0, 5.0]


# ---------------------------------------------------------------- 第二轮评审
# 内置分镜模板的内容哈希（统一成 LF 后 sha256 前 16 位）和规则版本绑在一起
TEMPLATE_HASH_FOR_RULES = {2: "7357bca01548d446", 3: "8436da3c14f43567"}


def test_builtin_template_hash_is_bound_to_rules_version():
    import hashlib

    from backend.llm.prompts import BUILTIN_DIR
    from backend.pipeline.storyboard import STORYBOARD_RULES_VERSION

    text = (BUILTIN_DIR / "storyboard.md").read_text(encoding="utf-8").replace("\r\n", "\n")
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    assert TEMPLATE_HASH_FOR_RULES.get(STORYBOARD_RULES_VERSION) == h, (
        f"改了分镜模板要把 STORYBOARD_RULES_VERSION 加一，并更新这里的哈希（现在是 {h}）")


def test_prompt_renders_project_aspect(tmp_path, db):
    ctx, ep_id, prov = setup(tmp_path, db, [shots_json(2)] * 4)
    run_storyboard(ctx, {"episode_id": ep_id})
    assert "画面是 9:16 竖屏" in prov.calls[0]["system"]
    with db.session() as s:
        s.get(Episode, ep_id).project.aspect = "16:9"
    # 画幅是输入：改了就重出
    assert run_storyboard(ctx, {"episode_id": ep_id})["blocks_done"] == 2
    assert "画面是 16:9 横屏" in prov.calls[2]["system"]


def test_storyboard_prompt_round2_rules():
    from backend.llm.prompts import PromptLibrary
    from backend.services import BUILTIN_DIR

    system, _ = PromptLibrary(BUILTIN_DIR, BUILTIN_DIR / "none").render(
        "storyboard", anchor="A", characters=[], location=None, text="x", count=3,
        shot_seconds=5, max_seconds=10, prev_action="", block_seconds=15.0)
    # 外貌并进「主体」那一步：第一次出现写外貌占位符，排在环境前面
    assert "人物第一次出现时写他的外貌占位符" in system and "2. 主体 + 动作瞬间" in system
    assert "{{char:3}}" in system and "不要再自己写他的脸" in system
    assert "4. 外貌" not in system and system.index("2. 主体") < system.index("4. 环境")
    assert "镜头数固定；放不下时优先保留插入镜头和主线动作，次要动作省略" in system
    assert "镜头不够就把次要的动作合并" not in system
    assert "motion_prompt 里用简短指代就够了" in system
    assert "image_prompt 和 motion_prompt 都逐字重复" not in system


def test_quoted_character_name_is_not_exempt():
    from types import SimpleNamespace as NS

    from backend.pipeline.storyboard import cjk_problems

    sh = NS(image_prompt='a man whose name tag reads "林夏" stands', motion_prompt="x",
            audio_prompt='a sign reading "老周修车"', props=[])
    out = cjk_problems([sh], ["林夏", "老周"])
    assert any("image_prompt" in x and "林夏" in x for x in out)
    assert any("audio_prompt" in x and "老周" in x for x in out)
    assert cjk_problems([NS(image_prompt='a sign reading "修车"', motion_prompt="x",
                            audio_prompt="x", props=[])], ["林夏", "老周"]) == []


def test_same_as_error_text_matches_short_reference_rule():
    import pytest
    from pydantic import ValidationError

    from backend.llm.schemas import ShotOut

    with pytest.raises(ValidationError) as ei:
        ShotOut(action="a", image_prompt="same as before " + "word " * 80, motion_prompt="m")
    msg = str(ei.value)
    assert "简短指代" in msg and "完整复述外貌" not in msg


# ---------------------------------------------------------------- v3：拼音名、外貌占位符、说话人、语速
def test_pinyin_patterns():
    from backend.pipeline.storyboard import pinyin_hits, pinyin_variants

    assert pinyin_variants("老周") == [["lao", "zhou"]]
    assert pinyin_variants("林小满") == [["lin", "xiao", "man"], ["xiao", "man"]]
    assert pinyin_variants("周") == [] and pinyin_variants("Tom") == []
    text = "Lao Zhou rolls the scooter; LAOZHOU nods; lao-zhou waits; Lin Xiaoman and xiaoman"
    hits = [w for _, w in pinyin_hits(text, ["老周", "林小满"])]
    assert hits == ["Lao Zhou", "LAOZHOU", "lao-zhou", "Lin Xiaoman", "xiaoman"]
    # 正常英文不误伤
    assert pinyin_hits("the man at the linen shop, a slow zoom", ["老周", "林小满"]) == []


def test_pinyin_name_in_motion_prompt_is_rejected_then_retried(tmp_path, db):
    bad = shots_json(2)
    bad["shots"][1]["motion_prompt"] = "Lin Xia looks up slowly"
    ctx, ep_id, prov = setup(tmp_path, db, [bad, shots_json(2), shots_json(2)])
    run_storyboard(ctx, {"episode_id": ep_id})
    err = prov.calls[1]["messages"][2]["content"]
    assert "第 2 镜 motion_prompt 里写了角色名的拼音 Lin Xia（林夏）" in err


def test_pinyin_name_is_replaced_on_last_attempt(tmp_path, db):
    from backend.llm.gateway import MAX_RETRIES

    bad = shots_json(2)
    bad["shots"][0]["audio_prompt"] = "quiet hum, Linxia speaks softly"
    ctx, ep_id, prov = setup(tmp_path, db, ["不是 JSON"] * MAX_RETRIES + [bad, shots_json(2)])
    run_storyboard(ctx, {"episode_id": ep_id})
    with db.session() as s:
        a = s.get(Episode, ep_id).shots[0].audio_prompt
    assert "Linxia" not in a and "the woman in her late twenties speaks softly" in a
    assert any("已换成英文外貌简称" in m for m in ctx.logs)


def test_cast_member_needs_placeholder(tmp_path, db):
    bad = shots_json(2)
    bad["shots"][0]["image_prompt"] = bad["shots"][0]["image_prompt"].replace("{{char:1}}", "")
    bad["shots"][1]["motion_prompt"] = "{{char:1}} looks up"
    ctx, ep_id, prov = setup(tmp_path, db, [bad, shots_json(2), shots_json(2)])
    run_storyboard(ctx, {"episode_id": ep_id})
    err = prov.calls[1]["messages"][2]["content"]
    assert "第 1 镜 cast 里有 林夏，image_prompt 里要写他的外貌占位符（{{char:1}}）" in err
    assert "第 2 镜 motion_prompt 里不能写外貌占位符" in err


def test_unknown_speaker_and_fast_dialogue_are_rejected(tmp_path, db):
    bad = shots_json(2)
    bad["shots"][0]["dialogue"] = [{"speaker": "路人", "zh": "让一让", "start": 0.5, "end": 1.5}]
    bad["shots"][1]["dialogue"] = [{"speaker": "林夏", "zh": "我早就知道是你改了提词器", "start": 0.5,
                                    "end": 2.0}]
    ctx, ep_id, prov = setup(tmp_path, db, [bad, shots_json(2), shots_json(2)])
    run_storyboard(ctx, {"episode_id": ep_id})
    err = prov.calls[1]["messages"][2]["content"]
    assert "说话人 路人 不在角色表里" in err
    assert "在 1.5 秒里说 12 个字太快" in err


def test_prompt_lists_placeholders_and_bans_pinyin(tmp_path, db):
    ctx, ep_id, prov = setup(tmp_path, db, [shots_json(2), shots_json(2)])
    run_storyboard(ctx, {"episode_id": ep_id})
    system, user = prov.calls[0]["system"], prov.calls[0]["messages"][0]["content"]
    assert "- 林夏｜{{char:1}}｜" + LOOK in user
    assert "拼音也不行" in system and "Lao Zhou" in system
    assert "slow push in" in system and "orbit" not in system
    assert "不要写胶片型号、颗粒" in system


def test_placeholder_resolves_by_shot_size_and_look_change_reaches_keyframes(tmp_path, db):
    """镜头里是占位符：首帧提示词按当前外貌、按景别填；换造型后首帧过期，分镜不用重写。"""
    from backend.pipeline.prompting import keyframe_input_hash, resolve_char_tokens, shot_image_prompt
    from backend.pipeline.impact import rerun_impact

    look = ("Chinese woman in her late twenties with an oval face and shoulder length black hair. "
            "Charcoal wool blazer over a white silk blouse. Black slim trousers and white sneakers.")
    assert resolve_char_tokens("x. {{char:1}} She sits.", {1: look}, "全景") == f"x. {look} She sits."
    close = resolve_char_tokens("x. {{char:1}} She sits.", {1: look}, "特写")
    assert "oval face" in close and "blazer" not in close and "sneakers" not in close
    mid = resolve_char_tokens("x. {{char:1}} She sits.", {1: look}, "近景")
    assert "blazer" in mid and "sneakers" not in mid
    assert resolve_char_tokens("x. {{char:9}} She sits.", {1: look}) == "x. She sits."

    ctx, ep_id, prov = setup(tmp_path, db, [shots_json(2), shots_json(2)])
    run_storyboard(ctx, {"episode_id": ep_id})
    with db.session() as s:
        ep = s.get(Episode, ep_id)
        sh, proj = ep.shots[0], ep.project
        assert "{{char:1}}" in sh.image_prompt
        assert LOOK in shot_image_prompt(sh, proj) and "{{" not in shot_image_prompt(sh, proj)
        before = keyframe_input_hash(sh, proj, proj.style_bible, "zimage")
        s.scalar(select(Character)).visual_prompt = "a tall man in his forties with a shaved head"
    with db.session() as s:
        ep = s.get(Episode, ep_id)
        sh, proj = ep.shots[0], ep.project
        assert "shaved head" in shot_image_prompt(sh, proj)
        assert keyframe_input_hash(sh, proj, proj.style_bible, "zimage") != before
    # 外貌不进新格式的分镜指纹：换造型不会让分镜重写（不删镜头）
    with db.session() as s:
        assert rerun_impact(s, ctx.services, ep_id, "storyboard", False).will_run is False
    assert run_storyboard(ctx, {"episode_id": ep_id}) == {"blocks_done": 0, "blocks_skipped": 2}


def test_old_v2_records_still_compare_looks(tmp_path, db):
    """v2（外貌抄进 image_prompt）出的分镜：记录里没有 placeholders，外貌仍按老格式进指纹。"""
    from backend.pipeline.storyboard import block_fps, build_storyboard_plan, pick_fp

    ctx, ep_id, prov = setup(tmp_path, db, [shots_json(2), shots_json(2)])
    run_storyboard(ctx, {"episode_id": ep_id})
    with db.session() as s:
        ep = s.get(Episode, ep_id)
        recs = {k: v for k, v in ep.stage_hashes.items() if k.startswith("storyboard:")}
        assert all(r["placeholders"] is True for r in recs.values())
        plan = build_storyboard_plan(s, ctx.services, ep_id)
        blk = plan.blocks[0]
        new, old = block_fps(plan, blk, "")
        assert new != old and pick_fp({"inputs": "x"}, new, old) == old
        assert pick_fp({"placeholders": True}, new, old) == new


def test_storyboard_prompt_gaze_and_small_details():
    from backend.llm.prompts import PromptLibrary
    from backend.services import BUILTIN_DIR

    system, _ = PromptLibrary(BUILTIN_DIR, BUILTIN_DIR / "none").render(
        "storyboard", anchor="A", characters=[], location=None, text="x", count=3,
        shot_seconds=5, max_seconds=10, prev_action="", block_seconds=15.0)
    assert "人物不看镜头，除非剧本写了看镜头" in system and "glancing up toward" in system
    assert "中景、全景不写手上的细节和小饰品" in system
    assert "with both arms extended" in system
