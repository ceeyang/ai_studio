def test_create_project_with_chinese_title(api):
    r = api.post("/api/projects", json={"title": "提词员 第一季"})
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["slug"] == "提词员-第一季" and p["aspect"] == "9:16"
    # 没传引擎：按默认风格预设（真人写实）推荐的引擎
    assert p["image_engine"] == "h3_image" and p["video_engine"] == "h3_video"
    assert (api.app.state.services.media.root / "提词员-第一季").is_dir()
    assert [x["slug"] for x in api.get("/api/projects").json()] == ["提词员-第一季"]
    assert api.get("/api/projects/提词员-第一季").json()["title"] == "提词员 第一季"


def test_project_validation(api):
    assert api.post("/api/projects", json={"title": "a", "slug": "demo"}).status_code == 201
    assert api.post("/api/projects", json={"title": "b", "slug": "demo"}).status_code == 409
    r = api.post("/api/projects", json={"title": "c", "video_engine": "sora"})
    assert r.status_code == 400 and "sora" in r.json()["detail"]
    assert api.post("/api/projects", json={"title": "d", "slug": "a/b"}).status_code == 400
    assert api.post("/api/projects", json={"title": "e", "aspect": "4:3"}).status_code == 422
    assert api.get("/api/projects/nope").status_code == 404


def test_patch_and_delete_project(api):
    api.post("/api/projects", json={"title": "demo"})
    r = api.patch("/api/projects/demo", json={"video_engine": "wan22_i2v", "seed": 7})
    assert r.json()["video_engine"] == "wan22_i2v" and r.json()["seed"] == 7
    assert api.patch("/api/projects/demo", json={"image_engine": "h3_video"}).status_code == 400
    assert api.delete("/api/projects/demo").status_code == 204
    assert api.get("/api/projects/demo").status_code == 404


def test_patch_project_rename_and_archive(api):
    api.post("/api/projects", json={"title": "demo"})
    r = api.patch("/api/projects/demo", json={"title": "改名了"})
    assert r.status_code == 200 and r.json()["title"] == "改名了" and r.json()["archived"] is False
    r = api.patch("/api/projects/demo", json={"archived": True})
    assert r.status_code == 200 and r.json()["archived"] is True
    r = api.patch("/api/projects/demo", json={"archived": False})
    assert r.status_code == 200 and r.json()["archived"] is False


def test_archived_projects_hidden_by_default(api):
    api.post("/api/projects", json={"title": "在用"})
    api.post("/api/projects", json={"title": "封存"})
    api.patch("/api/projects/封存", json={"archived": True})

    r = api.get("/api/projects")
    assert [x["slug"] for x in r.json()] == ["在用"]

    r = api.get("/api/projects", params={"archived": 1})
    assert sorted(x["slug"] for x in r.json()) == ["在用", "封存"]
    assert next(x for x in r.json() if x["slug"] == "封存")["archived"] is True


def test_episodes_crud(api):
    api.post("/api/projects", json={"title": "demo"})
    e1 = api.post("/api/projects/demo/episodes", json={"source_text": "梗概", "target_seconds": 30}).json()
    e2 = api.post("/api/projects/demo/episodes", json={"title": "第二集"}).json()
    assert (e1["number"], e2["number"]) == (1, 2)
    assert api.post("/api/projects/demo/episodes", json={"number": 2}).status_code == 409
    d = api.get("/api/projects/demo/episodes/by-number/1").json()
    assert d["id"] == e1["id"] and d["shots"] == [] and d["scene_blocks"] == []
    r = api.patch(f"/api/episodes/{e1['id']}", json={"title": "改名"})
    assert r.json()["title"] == "改名" and r.json()["source_text"] == "梗概"
    assert [e["number"] for e in api.get("/api/projects/demo/episodes").json()] == [1, 2]
    assert api.delete(f"/api/episodes/{e2['id']}").status_code == 204
    assert api.get(f"/api/episodes/{e2['id']}").status_code == 404


def test_delete_episode_removes_shots_slots_and_exports(api):
    from sqlalchemy import select

    from backend.domain.models import Candidate, Export, Shot, Slot
    from backend.domain.slots import add_candidate, get_or_create_slot

    api.post("/api/projects", json={"title": "demo"})
    ep = api.post("/api/projects/demo/episodes", json={"source_text": "x"}).json()
    sv = api.app.state.services
    with sv.db.session() as s:
        shot = Shot(episode_id=ep["id"], order=0, image_prompt="p")
        s.add(shot)
        s.flush()
        shot_id = shot.id
        slot = get_or_create_slot(s, "shot", shot_id, "first_frame")
        add_candidate(s, slot, file_path="x.png", engine="e", params={}, input_hash="h",
                      job_id=None)
        s.add(Export(episode_id=ep["id"], variant="9x16", file_path="demo/exports/x.mp4",
                     settings={}))
        s.flush()

    assert api.delete(f"/api/episodes/{ep['id']}").status_code == 204
    assert api.get(f"/api/episodes/{ep['id']}").status_code == 404

    with sv.db.session() as s:
        assert s.get(Shot, shot_id) is None
        assert list(s.scalars(select(Slot).where(Slot.owner_type == "shot",
                                                 Slot.owner_id == shot_id))) == []
        assert list(s.scalars(select(Candidate))) == []
        assert list(s.scalars(select(Export).where(Export.episode_id == ep["id"]))) == []


def test_delete_episode_refused_while_job_active(api):
    from sqlalchemy import select

    from backend.domain.models import Job

    api.post("/api/projects", json={"title": "demo"})
    ep = api.post("/api/projects/demo/episodes", json={"source_text": "x"}).json()
    sv = api.app.state.services
    with sv.db.session() as s:
        s.add(Job(type="export.episode", status="queued", owner_type="episode",
                  owner_id=ep["id"], project_id=None))
        s.flush()

    r = api.delete(f"/api/episodes/{ep['id']}")
    assert r.status_code == 409
    assert "任务" in r.json()["detail"]
    assert api.get(f"/api/episodes/{ep['id']}").status_code == 200

    with sv.db.session() as s:
        job = s.scalar(select(Job).where(Job.owner_type == "episode", Job.owner_id == ep["id"]))
        job.status = "done"
        s.flush()
    assert api.delete(f"/api/episodes/{ep['id']}").status_code == 204


def test_assets_and_style(api):
    from backend.domain.models import Character, Episode, Location, Project, SceneBlock

    api.post("/api/projects", json={"title": "demo"})
    ep = api.post("/api/projects/demo/episodes", json={"source_text": "x"}).json()
    db = api.app.state.services.db
    with db.session() as s:
        pid = s.query(Project).one().id
        s.add(Character(project_id=pid, name="林夏"))
        loc = Location(project_id=pid, name="直播间")
        s.add(loc)
        s.flush()
        ep_obj = s.get(Episode, ep["id"])
        ep_obj.scene_blocks.append(SceneBlock(order=0, text="段落", location_id=loc.id))
    ch = api.get("/api/projects/demo/characters").json()[0]
    assert api.patch(f"/api/characters/{ch['id']}", json={"visual_prompt": "LOOK"}).json()["visual_prompt"] == "LOOK"
    loc = api.get("/api/projects/demo/locations").json()[0]
    assert api.patch(f"/api/locations/{loc['id']}", json={"time_of_day": "夜"}).json()["time_of_day"] == "夜"
    assert api.get("/api/projects/demo/style").json() is None
    st = api.put("/api/projects/demo/style", json={"anchor_text": "ANCHOR", "mood_keywords": ["tense"]}).json()
    assert st["anchor_text"] == "ANCHOR" and api.get("/api/projects/demo/style").json()["mood_keywords"] == ["tense"]
    blk = api.get(f"/api/episodes/{ep['id']}").json()["scene_blocks"][0]
    assert api.patch(f"/api/scene-blocks/{blk['id']}", json={"text": "改过"}).json()["text"] == "改过"


def test_cross_origin_writes_rejected(api):
    r = api.post("/api/projects", json={"title": "evil"}, headers={"Origin": "http://evil.com"})
    assert r.status_code == 403
    assert api.get("/api/projects").json() == []
    assert api.post("/api/projects", json={"title": "a"}).status_code == 201
    for origin in ("http://127.0.0.1:5173", "http://localhost", "https://localhost:8700"):
        assert api.patch("/api/projects/a", json={"seed": 1},
                         headers={"Origin": origin}).status_code == 200, origin
    for origin in ("null", "http://localhost.evil.com", "http://127.0.0.1.evil.com:80"):
        assert api.delete("/api/projects/a", headers={"Origin": origin}).status_code == 403, origin
    # 读请求不看 Origin
    assert api.get("/api/projects", headers={"Origin": "http://evil.com"}).status_code == 200


def test_unknown_host_rejected(api):
    assert api.get("/api/health", headers={"Host": "evil.com"}).status_code == 400
    assert api.get("/api/health", headers={"Host": "127.0.0.1:8700"}).status_code == 200
    assert api.get("/api/health", headers={"Host": "localhost:8700"}).status_code == 200
