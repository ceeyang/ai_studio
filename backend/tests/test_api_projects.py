def test_create_project_with_chinese_title(api):
    r = api.post("/api/projects", json={"title": "提词员 第一季"})
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["slug"] == "提词员-第一季" and p["aspect"] == "9:16"
    assert p["image_engine"] == "zimage" and p["video_engine"] == "h3_video"
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
