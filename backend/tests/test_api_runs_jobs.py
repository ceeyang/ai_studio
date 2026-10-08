from backend.domain.models import Shot
from backend.domain.shots import renumber_shots
from backend.domain.slots import add_candidate, get_or_create_slot


def seed(api, n=2):
    api.post("/api/projects", json={"title": "demo"})
    ep = api.post("/api/projects/demo/episodes", json={"source_text": "梗概"}).json()
    with api.app.state.services.db.session() as s:
        for i in range(n):
            s.add(Shot(episode_id=ep["id"], order=i, image_prompt="p"))
        s.flush()
        renumber_shots(s, ep["id"])
    return ep["id"]


def test_run_keyframes_and_videos(api):
    ep_id = seed(api)
    r = api.post(f"/api/episodes/{ep_id}/run", json={"stage": "keyframes", "candidates": 2})
    assert r.status_code == 200 and len(r.json()["job_ids"]) == 4
    jobs = api.get("/api/jobs", params={"status": "queued"}).json()
    assert {j["type"] for j in jobs} == {"image.keyframe"} and all(j["gpu"] for j in jobs)
    r = api.post(f"/api/episodes/{ep_id}/run", json={"stage": "videos", "shots": ["S02"]})
    [vid] = r.json()["job_ids"]
    assert api.get(f"/api/jobs/{vid}").json()["model_family"] == "h3"


def test_run_all_and_errors(api):
    ep_id = seed(api)
    [jid] = api.post(f"/api/episodes/{ep_id}/run", json={"stage": "all"}).json()["job_ids"]
    j = api.get(f"/api/jobs/{jid}").json()
    assert j["type"] == "script"
    assert j["payload"]["then"] == ["storyboard", "keyframes", "videos", "export"]
    r = api.post(f"/api/episodes/{ep_id}/run", json={"stage": "keyframes", "shots": ["S09"]})
    assert r.status_code == 400 and "S09" in r.json()["detail"]
    assert api.post(f"/api/episodes/{ep_id}/run", json={"stage": "render"}).status_code == 422
    assert api.post("/api/episodes/999/run", json={"stage": "script"}).status_code == 404


def test_jobs_cancel_retry_input_and_queue(api):
    ep_id = seed(api)
    [jid] = api.post(f"/api/episodes/{ep_id}/run", json={"stage": "script"}).json()["job_ids"]
    assert api.post(f"/api/jobs/{jid}/cancel").json() == {"ok": True}
    assert api.get(f"/api/jobs/{jid}").json()["status"] == "canceled"
    new = api.post(f"/api/jobs/{jid}/retry", json={"payload": {"force": True}}).json()["job_id"]
    assert api.get(f"/api/jobs/{new}").json()["payload"]["force"] is True
    assert api.post(f"/api/jobs/{new}/input", json={"text": "x"}).status_code == 400
    assert api.post("/api/queue/pause").json()["paused"] is True
    assert api.get("/api/queue").json()["counts"]["queued"] == 1
    assert api.post("/api/queue/resume").json()["paused"] is False
    assert api.get("/api/jobs", params={"project": "demo"}).json()[0]["id"] == new
    assert api.get("/api/jobs/9999").status_code == 404


def test_candidates_select_and_media(api):
    ep_id = seed(api, n=1)
    sv = api.app.state.services
    shot_id = api.get(f"/api/episodes/{ep_id}").json()["shots"][0]["id"]
    files = []
    with sv.db.session() as s:
        slot = get_or_create_slot(s, "shot", shot_id, "first_frame")
        ids = []
        for i in range(2):
            p = sv.media.new_media_path("demo", "first_frame", "png")
            p.write_bytes(f"img{i}".encode())
            files.append(p)
            ids.append(add_candidate(s, slot, file_path=sv.media.rel(p), engine="zimage", params={},
                                     input_hash="h", job_id=None).id)
    r = api.post(f"/api/candidates/{ids[1]}/select")
    assert r.status_code == 200 and r.json()["id"] == ids[1]
    [slot] = api.get("/api/slots", params={"owner_type": "shot", "owner_id": shot_id}).json()
    assert slot["selected_candidate_id"] == ids[1]
    assert api.patch(f"/api/candidates/{ids[1]}", json={"note": "好", "rating": 5}).json()["rating"] == 5
    assert api.get(r.json()["url"]).content == b"img1"
    assert api.get(f"/api/candidates/{ids[0]}/file").content == b"img0"
    assert api.get("/media/studio.db").status_code == 403
    assert api.get("/media/demo/runs/1/workflow.api.json").status_code == 403
    assert api.post("/api/candidates/999/select").status_code == 404


def test_system_endpoints(api):
    assert api.get("/api/health").json()["ok"] is True
    eng = api.get("/api/engines").json()
    assert [e["name"] for e in eng["video"]] == ["h3_video", "wan22_i2v"]
    assert eng["video"][0]["caps"]["has_audio"] is True
    cfg = api.get("/api/config").json()
    assert cfg["server"]["port"] == 8700 and cfg["llm"]["default"] == "manual"


def test_websocket_streams_job_events(api):
    ep_id = seed(api)
    with api.websocket_connect("/api/ws") as ws:
        first = ws.receive_json()
        assert first["type"] == "queue" and "counts" in first
        api.post(f"/api/episodes/{ep_id}/run", json={"stage": "script"})
        msg = ws.receive_json()
        assert msg["type"] == "job" and msg["job"]["type"] == "script"
