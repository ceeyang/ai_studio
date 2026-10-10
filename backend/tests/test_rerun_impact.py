"""重跑影响预估：不调 LLM，靠和 pipeline 共用的指纹判断来估算会不会跑、会删什么。"""
from sqlalchemy import select

from backend.domain.models import Character, Episode, Location, Project, SceneBlock, Shot, StyleBible
from backend.domain.slots import add_candidate, get_or_create_slot
from backend.pipeline.impact import rerun_impact
from backend.pipeline.script import run_script
from backend.pipeline.storyboard import run_storyboard
from backend.tests.helpers import FakeCtx, make_gateway, make_services, seed_episode
from backend.tests.test_script import STRUCTURE, VISUAL
from backend.tests.test_storyboard import LOOK, seed_scripted, shots_json


def seed_one_block(tmp_path, db, texts):
    """1 个段落，预算刚好 2 个镜头（target=10, shot_seconds=5）。"""
    gw, prov = make_gateway(texts)
    ctx = FakeCtx(make_services(tmp_path, db, llm=gw))
    proj_id, ep_id = seed_episode(db, target=10.0)
    with db.session() as s:
        proj = s.get(Project, proj_id)
        proj.style_bible = StyleBible(anchor_text="ANCHOR photorealistic drama")
        s.add(Character(project_id=proj_id, name="林夏", visual_prompt=LOOK))
        loc = Location(project_id=proj_id, name="直播间", time_of_day="夜晚", visual_prompt="ROOM")
        s.add(loc)
        s.flush()
        ep = s.get(Episode, ep_id)
        ep.scene_blocks.append(SceneBlock(order=0, location_id=loc.id, text="甲" * 50))
    return ctx, ep_id, prov


def test_rerun_impact_counts(tmp_path, db):
    ctx, ep_id, _ = seed_one_block(tmp_path, db, [shots_json(2)])
    run_storyboard(ctx, {"episode_id": ep_id})
    sv = ctx.services
    with db.session() as s:
        shots = s.get(Episode, ep_id).shots
        assert len(shots) == 2
        target = shots[0]
        add_candidate(s, get_or_create_slot(s, "shot", target.id, "first_frame"),
                      file_path="demo/media/first_frame/x.png", engine="zimage", params={},
                      input_hash="h1", job_id=None)
        add_candidate(s, get_or_create_slot(s, "shot", target.id, "video"),
                      file_path="demo/media/video/x.mp4", engine="h3_video", params={},
                      input_hash="h2", job_id=None)
        s.add(Shot(episode_id=ep_id, order=999, action="手工加的", origin="manual"))

    with db.session() as s:
        impact = rerun_impact(s, sv, ep_id, "storyboard", True)
    assert impact.stage == "storyboard"
    assert impact.will_run is True
    assert impact.blocks == [1]
    assert impact.shots_deleted == 2
    assert impact.selected_first_frames == 1
    assert impact.selected_videos == 1
    assert impact.manual_kept == 1
    assert impact.overwrites == []

    with db.session() as s:
        impact2 = rerun_impact(s, sv, ep_id, "storyboard", False)
    assert impact2.will_run is False
    assert impact2.blocks == []
    assert impact2.shots_deleted == 0
    assert impact2.manual_kept == 1


def test_rerun_impact_storyboard_partial_block_change(tmp_path, db):
    """2 个段落都没动过指纹，只有其中一段文本变了才会重跑，不force 时不该整段都算。"""
    gw, prov = make_gateway([shots_json(2), shots_json(2)])
    ctx = FakeCtx(make_services(tmp_path, db, llm=gw))
    ep_id = seed_scripted(db)
    run_storyboard(ctx, {"episode_id": ep_id})
    sv = ctx.services
    with db.session() as s:
        ep = s.get(Episode, ep_id)
        ep.scene_blocks[1].text = "乙" * 60  # 改了第二段文本，指纹会变
    with db.session() as s:
        impact = rerun_impact(s, sv, ep_id, "storyboard", False)
    assert impact.will_run is True
    assert impact.blocks == [2]
    assert impact.shots_deleted == 2  # 只删第二段的 2 个 llm 镜头
    assert impact.manual_kept == 0


def test_rerun_impact_script_stage(tmp_path, db):
    gw, prov = make_gateway([STRUCTURE, VISUAL])
    sv = make_services(tmp_path, db, llm=gw)
    ctx = FakeCtx(sv)
    _, ep_id = seed_episode(db, source="林夏在直播间发现提词器被人改了。")
    run_script(ctx, {"episode_id": ep_id})

    with db.session() as s:
        unchanged = rerun_impact(s, sv, ep_id, "script", False)
    assert unchanged.will_run is False
    assert unchanged.overwrites == []
    assert unchanged.shots_deleted == 0

    with db.session() as s:
        forced = rerun_impact(s, sv, ep_id, "script", True)
    assert forced.will_run is True
    assert forced.blocks == []
    assert set(forced.overwrites) == {"角色描述", "场景氛围", "角色外观", "场景外观", "风格设定"}


def test_rerun_impact_script_visual_only_change(tmp_path, db):
    """只改了场景氛围（视觉设定会用到），原文没变：结构不重跑、不牵连镜头，只重跑视觉设定。"""
    gw, prov = make_gateway([STRUCTURE, VISUAL])
    sv = make_services(tmp_path, db, llm=gw)
    ctx = FakeCtx(sv)
    _, ep_id = seed_episode(db, source="林夏在直播间发现提词器被人改了。")
    run_script(ctx, {"episode_id": ep_id})
    with db.session() as s:
        ep = s.get(Episode, ep_id)
        loc = s.scalar(select(Location).where(Location.project_id == ep.project_id,
                                              Location.name == "直播间"))
        loc.atmosphere = "更紧张了"

    with db.session() as s:
        impact = rerun_impact(s, sv, ep_id, "script", False)
    assert impact.will_run is True
    assert impact.shots_deleted == 0  # 段落没清空，不牵连镜头
    assert set(impact.overwrites) == {"角色外观", "场景外观", "风格设定"}


def test_rerun_impact_script_deletes_all_llm_shots_when_structure_changes(tmp_path, db):
    gw, prov = make_gateway([STRUCTURE, VISUAL])
    sv = make_services(tmp_path, db, llm=gw)
    ctx = FakeCtx(sv)
    _, ep_id = seed_episode(db, source="林夏在直播间发现提词器被人改了。")
    run_script(ctx, {"episode_id": ep_id})
    with db.session() as s:
        ep = s.get(Episode, ep_id)
        block_id = ep.scene_blocks[0].id
        s.add(Shot(episode_id=ep_id, scene_block_id=block_id, order=0, origin="llm"))
        s.add(Shot(episode_id=ep_id, scene_block_id=block_id, order=1, origin="llm"))
        s.add(Shot(episode_id=ep_id, order=2, origin="manual"))

    with db.session() as s:
        impact = rerun_impact(s, sv, ep_id, "script", True)
    assert impact.will_run is True
    assert impact.shots_deleted == 2
    assert impact.manual_kept == 1


def test_api_rerun_impact_endpoint(api):
    api.post("/api/projects", json={"title": "demo"})
    ep = api.post("/api/projects/demo/episodes", json={"source_text": "x", "target_seconds": 10}).json()
    ep_id = ep["id"]
    with api.app.state.services.db.session() as s:
        ep_obj = s.get(Episode, ep_id)
        ep_obj.scene_blocks.append(SceneBlock(order=0, text="段落甲" * 10))

    r = api.get(f"/api/episodes/{ep_id}/rerun-impact", params={"stage": "storyboard", "force": 1})
    assert r.status_code == 200
    body = r.json()
    assert body["stage"] == "storyboard" and body["will_run"] is True

    assert api.get(f"/api/episodes/{ep_id}/rerun-impact",
                   params={"stage": "script", "force": 0}).status_code == 200
    assert api.get(f"/api/episodes/{ep_id}/rerun-impact",
                   params={"stage": "bogus"}).status_code == 422
    assert api.get("/api/episodes/999/rerun-impact", params={"stage": "script"}).status_code == 404


def test_rerun_impact_block_with_only_manual_shots_will_run(tmp_path, db):
    ctx, ep_id, _ = seed_one_block(tmp_path, db, [shots_json(2)])
    run_storyboard(ctx, {"episode_id": ep_id})
    with db.session() as s:
        b1 = s.get(Episode, ep_id).scene_blocks[0].id
        for sh in s.scalars(select(Shot).where(Shot.scene_block_id == b1)):
            s.delete(sh)
        s.add(Shot(episode_id=ep_id, scene_block_id=b1, order=5, action="手工", origin="manual"))
    with db.session() as s:
        impact = rerun_impact(s, ctx.services, ep_id, "storyboard", False)
    assert impact.will_run is True and impact.blocks == [1]
    assert impact.shots_deleted == 0 and impact.manual_kept == 1
