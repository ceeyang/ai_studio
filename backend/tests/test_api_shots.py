from backend.domain.models import Project, Shot
from backend.domain.shots import renumber_shots
from backend.domain.slots import add_candidate, get_or_create_slot
from backend.pipeline.prompting import keyframe_input_hash, video_input_hash


def seed(api, n=3):
    api.post("/api/projects", json={"title": "demo"})
    ep = api.post("/api/projects/demo/episodes", json={"source_text": "x"}).json()
    db = api.app.state.services.db
    with db.session() as s:
        for i in range(n):
            s.add(Shot(episode_id=ep["id"], order=i, image_prompt=f"prompt {i}", duration=6.0,
                       dialogue=[{"speaker": "林", "zh": "前半句", "start": 0.5, "end": 2.0},
                                 {"speaker": "林", "zh": "后半句", "start": 3.5, "end": 5.0}],
                       transition_out="dissolve_0.5"))
        s.flush()
        renumber_shots(s, ep["id"])
    return ep["id"], [x["id"] for x in api.get(f"/api/episodes/{ep['id']}").json()["shots"]]


def codes(api, ep_id):
    return [(x["id"], x["code"]) for x in api.get(f"/api/episodes/{ep_id}").json()["shots"]]


def test_create_after_and_append(api):
    ep_id, ids = seed(api)
    r = api.post(f"/api/episodes/{ep_id}/shots", json={"after_shot_id": ids[0], "action": "插入"})
    assert r.status_code == 201 and r.json()["code"] == "S02"
    new = api.post(f"/api/episodes/{ep_id}/shots", json={"action": "末尾"}).json()
    assert codes(api, ep_id)[-1] == (new["id"], "S05")
    assert api.post(f"/api/episodes/{ep_id}/shots", json={"after_shot_id": 999}).status_code == 404


def test_patch_shot(api):
    ep_id, ids = seed(api)
    r = api.patch(f"/api/shots/{ids[1]}", json={"image_prompt": "新的", "video_engine": "wan22_i2v"})
    assert r.json()["image_prompt"] == "新的" and r.json()["video_engine"] == "wan22_i2v"
    assert r.json()["action"] == ""
    assert api.patch(f"/api/shots/{ids[1]}", json={"video_engine": "zimage"}).status_code == 400
    assert api.patch(f"/api/shots/{ids[1]}", json={"transition_out": "spin"}).status_code == 422
    assert api.patch(f"/api/shots/{ids[1]}", json={"video_engine": None}).json()["video_engine"] is None


def test_delete_and_reorder(api):
    ep_id, ids = seed(api)
    assert api.delete(f"/api/shots/{ids[0]}").status_code == 204
    assert codes(api, ep_id) == [(ids[1], "S01"), (ids[2], "S02")]
    r = api.post(f"/api/episodes/{ep_id}/shots/reorder", json={"shot_ids": [ids[2], ids[1]]})
    assert [x["id"] for x in r.json()] == [ids[2], ids[1]]
    assert api.post(f"/api/episodes/{ep_id}/shots/reorder", json={"shot_ids": [ids[2]]}).status_code == 400


def test_create_shot_is_manual_origin_and_split_copies_it(api):
    ep_id, ids = seed(api)
    manual = api.post(f"/api/episodes/{ep_id}/shots", json={"action": "手工加的"}).json()
    with api.app.state.services.db.session() as s:
        assert s.get(Shot, manual["id"]).origin == "manual"
        assert s.get(Shot, ids[0]).origin == "llm"  # 分镜产的镜头默认是 llm
    a, b = api.post(f"/api/shots/{manual['id']}/split").json()
    with api.app.state.services.db.session() as s:
        assert s.get(Shot, a["id"]).origin == "manual"
        assert s.get(Shot, b["id"]).origin == "manual"


def test_split_moves_later_dialogue(api):
    ep_id, ids = seed(api)
    a, b = api.post(f"/api/shots/{ids[0]}/split").json()
    assert (a["duration"], b["duration"]) == (3.0, 3.0)
    assert [d["zh"] for d in a["dialogue"]] == ["前半句"]
    assert b["dialogue"] == [{"speaker": "林", "zh": "后半句", "start": 0.5, "end": 2.0}]
    assert a["transition_out"] == "hard_cut" and b["transition_out"] == "dissolve_0.5"
    assert b["image_prompt"] == "prompt 0" and b["code"] == "S02"
    assert len(codes(api, ep_id)) == 4


def _candidate(s, shot_id, kind, h):
    return add_candidate(s, get_or_create_slot(s, "shot", shot_id, kind), file_path=f"demo/media/{kind}/{h}.bin",
                         engine="x", params={}, input_hash=h, job_id=None)


def test_first_frame_stale_after_prompt_edit(api):
    ep_id, ids = seed(api)
    db = api.app.state.services.db
    with db.session() as s:
        shot = s.get(Shot, ids[0])
        proj = s.query(Project).one()
        _candidate(s, ids[0], "first_frame", keyframe_input_hash(shot, proj, None, "zimage"))
    slot = api.get(f"/api/shots/{ids[0]}").json()["slots"][0]
    assert slot["kind"] == "first_frame" and slot["stale"] is False
    assert slot["candidates"][0]["url"].startswith("/media/demo/media/first_frame/")
    api.patch(f"/api/shots/{ids[0]}", json={"image_prompt": "改了"})
    assert api.get(f"/api/shots/{ids[0]}").json()["slots"][0]["stale"] is True


def test_video_stale_after_new_first_frame(api):
    ep_id, ids = seed(api)
    db = api.app.state.services.db
    with db.session() as s:
        shot = s.get(Shot, ids[0])
        proj = s.query(Project).one()
        h = keyframe_input_hash(shot, proj, None, "zimage")
        ff1 = _candidate(s, ids[0], "first_frame", h)
        _candidate(s, ids[0], "video", video_input_hash(shot, proj, None, ff1.id, "h3_video", True))
        ff2 = _candidate(s, ids[0], "first_frame", h)  # 同输入：不自动选
        ff2_id = ff2.id
    slots = {x["kind"]: x for x in api.get(f"/api/shots/{ids[0]}").json()["slots"]}
    assert slots["video"]["stale"] is False
    assert api.post(f"/api/candidates/{ff2_id}/select").status_code == 200
    slots = {x["kind"]: x for x in api.get(f"/api/shots/{ids[0]}").json()["slots"]}
    assert slots["first_frame"]["stale"] is False and slots["video"]["stale"] is True


def _slot_count(api):
    from sqlalchemy import func, select

    from backend.domain.models import Candidate, Slot
    with api.app.state.services.db.session() as s:
        return (s.scalar(select(func.count()).select_from(Slot)),
                s.scalar(select(func.count()).select_from(Candidate)))


def test_deleted_shot_slots_not_inherited(api):
    ep_id, ids = seed(api)
    with api.app.state.services.db.session() as s:
        _candidate(s, ids[-1], "video", "h")
        _candidate(s, ids[-1], "first_frame", "h")
    assert api.delete(f"/api/shots/{ids[-1]}").status_code == 204
    assert _slot_count(api) == (0, 0)
    new = api.post(f"/api/episodes/{ep_id}/shots", json={"action": "新镜头"}).json()
    assert new["id"] not in ids
    assert new["slots"] == []


def test_episode_and_project_delete_drop_slots(api):
    ep_id, ids = seed(api)
    with api.app.state.services.db.session() as s:
        _candidate(s, ids[0], "video", "h")
    assert api.delete(f"/api/episodes/{ep_id}").status_code == 204
    assert _slot_count(api) == (0, 0)
    ep2 = api.post("/api/projects/demo/episodes", json={"source_text": "y"}).json()
    with api.app.state.services.db.session() as s:
        sh = Shot(episode_id=ep2["id"], order=0)
        s.add(sh)
        s.flush()
        _candidate(s, sh.id, "first_frame", "h")
    assert api.delete("/api/projects/demo").status_code == 204
    assert _slot_count(api) == (0, 0)
