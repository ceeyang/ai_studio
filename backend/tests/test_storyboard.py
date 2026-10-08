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
