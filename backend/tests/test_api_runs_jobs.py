from backend.domain.models import Shot
from backend.domain.shots import renumber_shots
from backend.domain.slots import add_candidate, get_or_create_slot


def seed(api, n=2):
    api.post("/api/projects", json={"title": "demo", "image_engine": "zimage"})
    ep = api.post("/api/projects/demo/episodes", json={"source_text": "梗概"}).json()
    with api.app.state.services.db.session() as s:
        for i in range(n):
            s.add(Shot(episode_id=ep["id"], order=i, image_prompt="p"))
        s.flush()
        renumber_shots(s, ep["id"])
    return ep["id"]


def _cand(s, shot_id, kind, h="h", job_id=None, params=None):
    return add_candidate(s, get_or_create_slot(s, "shot", shot_id, kind),
                         file_path=f"demo/media/{kind}/{shot_id}_{h}.bin", engine="zimage",
                         params=params or {}, input_hash=h, job_id=job_id)


def run_next(api, runner):
    """把下一个排队的 GPU 任务拿出来同步跑一遍（测试里 worker 没启动）。"""
    sv, m = api.app.state.services, api.app.state.manager
    sv.comfy = runner
    jid = m._claim(gpu=True)
    assert jid is not None
    m._execute(jid)
    return jid


def test_run_keyframes_and_videos(api):
    ep_id = seed(api)
    r = api.post(f"/api/episodes/{ep_id}/run", json={"stage": "keyframes", "candidates": 2})
    assert r.status_code == 200 and len(r.json()["job_ids"]) == 4
    jobs = api.get("/api/jobs", params={"status": "queued"}).json()
    assert {j["type"] for j in jobs} == {"image.keyframe"} and all(j["gpu"] for j in jobs)
    shot2 = api.get(f"/api/episodes/{ep_id}").json()["shots"][1]["id"]
    with api.app.state.services.db.session() as s:
        _cand(s, shot2, "first_frame")
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
    assert [e["name"] for e in eng["video"]] == ["gemini_video", "h3_video", "muse_video", "sora_video", "veo_video",
                                                 "wan22_i2v"]
    assert eng["video"][0]["caps"]["has_audio"] is True
    assert eng["video"][0]["caps"]["duration_control"] == "fixed" and eng["video"][0]["runtime"] == "browser"
    api_rows = {e["name"]: e for e in eng["video"] + eng["image"] if e["runtime"] == "api"}
    assert set(api_rows) == {"sora_video", "veo_video", "openai_image", "gemini_api_image"}
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


def test_media_rejects_unsafe_paths_without_resolving(api, monkeypatch):
    import pathlib

    seen = []
    real_resolve = pathlib.Path.resolve

    def guarded(self, *a, **kw):
        seen.append(str(self))
        if str(self).startswith(("\\\\", "//")):
            raise AssertionError(f"不应解析网络路径：{self}")
        return real_resolve(self, *a, **kw)

    monkeypatch.setattr(pathlib.Path, "resolve", guarded)
    bad = ["/media/..%2Fstudio.db", "/media/demo/media/..%2F..%2Fstudio.db",
           "/media///host/share/x", "/media/%2F%2Fhost%2Fshare%2Fx", "/media/C:%2Fx",
           "/media/C:/Windows/win.ini", "/media/%5C%5Chost%5Cshare%5Cx",
           "/media/demo%5Cmedia%5Cx.png", "/media//etc/passwd"]
    for url in bad:
        r = api.get(url)
        assert r.status_code == 400, (url, r.status_code)
    assert seen == []  # 文本检查就拒绝了，没走到 resolve


def test_media_missing_file_is_404(api):
    api.post("/api/projects", json={"title": "demo"})
    assert api.get("/media/demo/media/first_frame/nope.png").status_code == 404


# ---------------------------------------------------------------- 提交前置校验
def test_videos_without_first_frame_rejected_at_submit(api):
    ep_id = seed(api)
    r = api.post(f"/api/episodes/{ep_id}/run", json={"stage": "videos"})
    assert r.status_code == 400 and "S01、S02 还没有选定首帧，先生成首帧" in r.json()["detail"]
    assert api.get("/api/jobs").json() == []
    shot1 = api.get(f"/api/episodes/{ep_id}").json()["shots"][0]["id"]
    with api.app.state.services.db.session() as s:
        _cand(s, shot1, "first_frame")
    r = api.post(f"/api/episodes/{ep_id}/run", json={"stage": "videos"})
    assert r.status_code == 400 and "S02 还没有选定首帧" in r.json()["detail"]
    assert "S01" not in r.json()["detail"]
    r = api.post(f"/api/episodes/{ep_id}/run", json={"stage": "videos", "shots": ["S01"]})
    assert r.status_code == 200
    r = api.post(f"/api/episodes/{ep_id}/run", json={"stage": "keyframes", "then": ["videos"]})
    assert r.status_code == 200 and len(r.json()["job_ids"]) == 4


def test_export_without_videos_rejected_at_submit(api):
    ep_id = seed(api)
    r = api.post(f"/api/episodes/{ep_id}/run", json={"stage": "export"})
    assert r.status_code == 400 and "以下镜头还没有选定视频：S01, S02" in r.json()["detail"]
    shot1 = api.get(f"/api/episodes/{ep_id}").json()["shots"][0]["id"]
    with api.app.state.services.db.session() as s:
        _cand(s, shot1, "video")
    r = api.post(f"/api/episodes/{ep_id}/run", json={"stage": "export"})
    assert r.status_code == 400 and r.json()["detail"].endswith("S02")
    r = api.post(f"/api/episodes/{ep_id}/run",
                 json={"stage": "keyframes", "then": ["videos", "export"]})
    assert r.status_code == 200


def test_comfy_down_rejected_at_submit(api, monkeypatch):
    from backend.engines.comfyui import health

    monkeypatch.setattr(health, "check_comfy",
                        lambda client: health.ComfyHealth(False, "连不上"))
    ep_id = seed(api)
    r = api.post(f"/api/episodes/{ep_id}/run", json={"stage": "keyframes"})
    assert r.status_code == 400
    assert r.json()["detail"] == "ComfyUI 连不上（http://127.0.0.1:9），先启动 ComfyUI 或到设置页检查"
    assert api.get("/api/jobs").json() == []
    # 网页出图引擎不走 ComfyUI：不拦
    shots = api.get(f"/api/episodes/{ep_id}").json()["shots"]
    for sh in shots:
        assert api.patch(f"/api/shots/{sh['id']}", json={"image_engine": "gpt_image"}).status_code == 200
    assert api.post(f"/api/episodes/{ep_id}/run", json={"stage": "keyframes"}).status_code == 200
    # 视频还是 ComfyUI（h3_video）：拦
    r = api.post(f"/api/episodes/{ep_id}/run", json={"stage": "keyframes", "then": ["videos"]})
    assert r.status_code == 400 and "ComfyUI 连不上" in r.json()["detail"]


# ---------------------------------------------------------------- 镜头带进行中 / 失败任务
def test_shot_detail_active_and_failure(api):
    from backend.queue.errors import JobError
    from backend.tests.helpers import FakeRunner

    ep_id = seed(api, n=1)
    [jid] = api.post(f"/api/episodes/{ep_id}/run", json={"stage": "keyframes"}).json()["job_ids"]
    shot = api.get(f"/api/episodes/{ep_id}").json()["shots"][0]
    assert ([(j["id"], j["type"], j["status"]) for j in shot["active_jobs"]]
            == [(jid, "image.keyframe", "queued")])
    assert shot["failures"] == []
    run_next(api, FakeRunner(fail=JobError("ComfyUI 报错：显存不足\n" + "x" * 500)))
    shot = api.get(f"/api/shots/{shot['id']}").json()
    assert shot["active_jobs"] == []
    [f] = shot["failures"]
    assert f["id"] == jid and f["type"] == "image.keyframe" and f["status"] == "failed"
    assert f["error"] == "ComfyUI 报错：显存不足" and f["finished_at"]
    # 再成功出一张：失败不再显示
    api.post(f"/api/episodes/{ep_id}/run", json={"stage": "keyframes"})
    run_next(api, FakeRunner())
    shot = api.get(f"/api/shots/{shot['id']}").json()
    assert shot["failures"] == [] and shot["active_jobs"] == []
    assert shot["slots"][0]["selected_candidate_id"] is not None


def test_failure_error_truncated(api):
    from backend.queue.errors import JobError
    from backend.tests.helpers import FakeRunner

    ep_id = seed(api, n=1)
    api.post(f"/api/episodes/{ep_id}/run", json={"stage": "keyframes"})
    run_next(api, FakeRunner(fail=JobError("长" * 300)))
    [f] = api.get(f"/api/episodes/{ep_id}").json()["shots"][0]["failures"]
    assert len(f["error"]) == 200


def test_new_candidates_count(api):
    from backend.tests.helpers import FakeRunner

    ep_id = seed(api, n=1)
    api.post(f"/api/episodes/{ep_id}/run", json={"stage": "keyframes", "candidates": 2})
    runner = FakeRunner()
    run_next(api, runner)
    run_next(api, runner)
    [slot] = api.get(f"/api/episodes/{ep_id}").json()["shots"][0]["slots"]
    assert len(slot["candidates"]) == 2 and slot["new_candidates"] == 1
    assert slot["stale"] is False and slot["stale_reason"] is None
    inputs = slot["candidates"][0]["params"]["inputs"]
    assert inputs["prompt"] == "p" and inputs["engine"] == "zimage" and inputs["aspect"] == "9:16"
    assert inputs["refs"] == [] and "negative" in inputs


# ---------------------------------------------------------------- 任务上下文
def test_job_context_for_shot_job(api):
    ep_id = seed(api)
    [_, jid] = api.post(f"/api/episodes/{ep_id}/run", json={"stage": "keyframes"}).json()["job_ids"]
    shot2 = api.get(f"/api/episodes/{ep_id}").json()["shots"][1]["id"]
    ctx = api.get(f"/api/jobs/{jid}").json()["context"]
    assert ctx["label"] == "demo · 第1集 · S02"
    assert ctx["url"] == f"/projects/demo/episodes/{ep_id}/director?shot={shot2}"
    assert ctx["project_slug"] == "demo" and ctx["episode_id"] == ep_id and ctx["shot_id"] == shot2
    listed = {j["id"]: j for j in api.get("/api/jobs").json()}
    assert listed[jid]["context"] == ctx and listed[jid]["payload"]["shot_id"] == shot2


def test_job_context_for_episode_and_export_jobs(api):
    ep_id = seed(api)
    [sjid] = api.post(f"/api/episodes/{ep_id}/run", json={"stage": "script"}).json()["job_ids"]
    ctx = api.get(f"/api/jobs/{sjid}").json()["context"]
    assert ctx["label"] == "demo · 第1集" and ctx["url"] == f"/projects/demo/episodes/{ep_id}/script"
    with api.app.state.services.db.session() as s:
        for sh in api.get(f"/api/episodes/{ep_id}").json()["shots"]:
            _cand(s, sh["id"], "video")
    [ejid] = api.post(f"/api/episodes/{ep_id}/run", json={"stage": "export"}).json()["job_ids"]
    ctx = api.get(f"/api/jobs/{ejid}").json()["context"]
    assert ctx["url"] == f"/projects/demo/episodes/{ep_id}/export"
    # 没有归属的任务：context 为空
    jid = api.app.state.manager.submit("script", {"episode_id": ep_id})
    assert api.get(f"/api/jobs/{jid}").json()["context"] is None


def test_run_regenerate_params(api):
    from backend.domain.models import Episode, SceneBlock
    api.post("/api/projects", json={"title": "demo"})
    ep_id = api.post("/api/projects/demo/episodes", json={"source_text": "梗概"}).json()["id"]
    other = api.post("/api/projects/demo/episodes", json={"source_text": "别集"}).json()["id"]
    with api.app.state.services.db.session() as s:
        ep, oth = s.get(Episode, ep_id), s.get(Episode, other)
        ep.scene_blocks.append(SceneBlock(order=0, text="甲"))
        oth.scene_blocks.append(SceneBlock(order=0, text="乙"))
        s.flush()
        mine, foreign = ep.scene_blocks[0].id, oth.scene_blocks[0].id
    url = f"/api/episodes/{ep_id}/run"

    [jid] = api.post(url, json={"stage": "storyboard", "scene_block_ids": [mine],
                                "note": "  更紧凑 ", "then": ["keyframes"]}).json()["job_ids"]
    p = api.get(f"/api/jobs/{jid}").json()["payload"]
    assert p["scene_block_ids"] == [mine] and p["note"] == "更紧凑" and p["force"] is True
    # 修改意见和按段只作用于本阶段，不带进后续阶段
    assert p["options"]["note"] is None and p["options"]["scene_block_ids"] is None

    [jid] = api.post(url, json={"stage": "script", "note": "更紧凑"}).json()["job_ids"]
    p = api.get(f"/api/jobs/{jid}").json()["payload"]
    assert p["note"] == "更紧凑" and p["force"] is True

    [jid] = api.post(url, json={"stage": "script", "note": "   "}).json()["job_ids"]
    p = api.get(f"/api/jobs/{jid}").json()["payload"]
    assert "note" not in p and p["force"] is False

    r = api.post(url, json={"stage": "script", "scene_block_ids": [mine]})
    assert r.status_code == 400 and "只有分镜能按段重写" in r.json()["detail"]
    r = api.post(url, json={"stage": "storyboard", "scene_block_ids": [foreign]})
    assert r.status_code == 400 and "段落" in r.json()["detail"]
    assert api.post(url, json={"stage": "script", "note": "长" * 501}).status_code == 422


# ---------------------------------------------------------------- 不强制的分镜/剧本运行：保护做过的镜头
def _seed_storyboarded(api, slug="demo"):
    """一集两段、每段一个 llm 镜头，分镜记录按新格式写好（S01 段输入没变，S02 段的输入指纹对不上）。"""
    from backend.domain.models import Episode, SceneBlock
    from backend.pipeline.impact import scan_storyboard
    from backend.pipeline.storyboard import (build_storyboard_plan, shot_content_hash,
                                             storyboard_inputs_fp, storyboard_rules)

    sv = api.app.state.services
    api.post("/api/projects", json={"title": slug})
    ep_id = api.post(f"/api/projects/{slug}/episodes", json={"source_text": "梗概"}).json()["id"]
    with sv.db.session() as s:
        ep = s.get(Episode, ep_id)
        ep.scene_blocks.append(SceneBlock(order=0, text="甲" * 30))
        ep.scene_blocks.append(SceneBlock(order=1, text="乙" * 30))
        s.flush()
        b1, b2 = ep.scene_blocks[0].id, ep.scene_blocks[1].id
        a = Shot(episode_id=ep_id, scene_block_id=b1, order=0, action="动作甲", image_prompt="p")
        b = Shot(episode_id=ep_id, scene_block_id=b2, order=1000, action="动作乙", image_prompt="p")
        s.add_all([a, b])
        s.flush()
        renumber_shots(s, ep_id)
        plan = build_storyboard_plan(s, sv, ep_id)
        rules = storyboard_rules(plan.prompts)
        blk = plan.blocks[0]
        fp1 = storyboard_inputs_fp(plan.anchor, plan.characters, blk["location"], blk["text"],
                                   blk["count"], plan.shot_seconds, plan.max_seconds, "",
                                   blk["seconds"])
        ep.stage_hashes = {
            f"storyboard:{b1}": {"inputs": fp1, "rules": rules,
                                 "shots": {str(a.id): shot_content_hash(a)}},
            f"storyboard:{b2}": {"inputs": "changed", "rules": rules,
                                 "shots": {str(b.id): shot_content_hash(b)}}}
        ids = (a.id, b.id)
    with sv.db.session() as s:
        assert scan_storyboard(s, sv, ep_id, False).block_ids == [b2]
    return ep_id, ids


def test_unforced_storyboard_run_refuses_to_delete_selected_shots(api):
    ep_id, (a, b) = _seed_storyboarded(api)
    url = f"/api/episodes/{ep_id}/run"
    # S02 那段会重跑，但 S02 没有选定、没改过：照常提交
    assert api.post(url, json={"stage": "storyboard"}).status_code == 200
    with api.app.state.services.db.session() as s:
        _cand(s, b, "video")
    r = api.post(url, json={"stage": "storyboard"})
    assert r.status_code == 400
    d = r.json()["detail"]
    assert "S02" in d and "选定" in d and "confirm=true" in d and "--yes" in d and "S01" not in d
    assert "历史候选" in d and "自动备份" in d
    assert api.post(url, json={"stage": "storyboard", "confirm": True}).status_code == 200
    # 强制 / 带修改意见：用户明确要重出（界面上的「重新生成」对话框已经先给看过影响）
    assert api.post(url, json={"stage": "storyboard", "force": True}).status_code == 200
    assert api.post(url, json={"stage": "storyboard", "note": "更紧凑"}).status_code == 200
    # 后面的阶段不受影响
    assert api.post(url, json={"stage": "keyframes", "shots": ["S01"]}).status_code == 200


def test_unforced_run_refuses_to_delete_edited_shots(api):
    ep_id, (a, b) = _seed_storyboarded(api)
    api.patch(f"/api/shots/{b}", json={"image_prompt": "用户改过的提示词"})
    r = api.post(f"/api/episodes/{ep_id}/run", json={"stage": "storyboard"})
    assert r.status_code == 400 and "S02 生成后改过" in r.json()["detail"]
    im = api.get(f"/api/episodes/{ep_id}/rerun-impact",
                 params={"stage": "storyboard", "force": 0}).json()
    assert im["edited_shots"] == 1 and im["shots_deleted"] == 1 and im["rules_outdated"] is False
    # 改的是不会重跑的 S01：不拦
    ep2, (a2, _) = _seed_storyboarded(api, "demo2")
    api.patch(f"/api/shots/{a2}", json={"image_prompt": "改过"})
    assert api.post(f"/api/episodes/{ep2}/run", json={"stage": "storyboard"}).status_code == 200


def test_unforced_all_run_counts_script_rerun_losses(api):
    """整集 all：剧本结构会重跑（还没有结构指纹）→ llm 镜头都会变孤儿被删，有选定的要确认。"""
    ep_id, (a, b) = _seed_storyboarded(api)
    with api.app.state.services.db.session() as s:
        _cand(s, a, "first_frame")
    url = f"/api/episodes/{ep_id}/run"
    r = api.post(url, json={"stage": "all"})
    assert r.status_code == 400 and "S01" in r.json()["detail"]
    assert api.post(url, json={"stage": "all", "confirm": True}).status_code == 200


def test_engines_mark_comfy_engines_unready_when_comfy_down(api, monkeypatch):
    from backend.engines.comfyui import health

    rows = {e["name"]: e for e in api.get("/api/engines").json()["video"]}
    assert rows["h3_video"]["ready"] is True and rows["h3_video"]["note"] is None
    monkeypatch.setattr(health, "check_comfy",
                        lambda client: health.ComfyHealth(False, "连不上 127.0.0.1:9（拒绝连接）"))
    eng = api.get("/api/engines").json()
    rows = {e["name"]: e for e in eng["video"] + eng["image"]}
    assert rows["h3_video"]["ready"] is False
    assert rows["h3_video"]["note"] == "ComfyUI 连不上 127.0.0.1:9（拒绝连接）"
    assert rows["zimage"]["ready"] is False
    # 设置页的可选项也一样
    choices = api.get("/api/settings").json()["choices"]["video"]
    assert {c["name"]: c for c in choices}["h3_video"]["ready"] is False
