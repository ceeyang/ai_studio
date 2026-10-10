"""成片列表：最新在前，带可直接下载的 /media 链接。"""
from backend.domain.models import Export


def test_list_exports_newest_first_with_media_url(api):
    api.post("/api/projects", json={"title": "提词员"})
    ep = api.post("/api/projects/提词员/episodes", json={"source_text": "x"}).json()
    sv = api.app.state.services
    paths = []
    with sv.db.session() as s:
        for i in range(2):
            p = sv.media.export_path("提词员", f"ep01_9x16_{i}.mp4")
            p.write_bytes(f"mp4-{i}".encode())
            rel = sv.media.rel(p)
            paths.append(rel)
            s.add(Export(episode_id=ep["id"], variant="9x16", file_path=rel,
                         settings={"clips": 2}))
            s.flush()
    rows = api.get(f"/api/episodes/{ep['id']}/exports").json()
    assert [r["file_path"] for r in rows] == paths[::-1]
    assert rows[0]["variant"] == "9x16" and rows[0]["settings"] == {"clips": 2}
    assert rows[0]["url"].startswith("/media/%E6%8F%90%E8%AF%8D%E5%91%98/exports/")
    assert api.get(rows[0]["url"]).content == b"mp4-1"


def test_list_exports_empty_and_missing_episode(api):
    api.post("/api/projects", json={"title": "demo"})
    ep = api.post("/api/projects/demo/episodes", json={}).json()
    assert api.get(f"/api/episodes/{ep['id']}/exports").json() == []
    assert api.get("/api/episodes/999/exports").status_code == 404


def test_export_label_matches_frontend_phrasing(api):
    """label 照抄前端 ExportsPanel 的拼法：画幅、字幕、配音、原声底、对齐、片尾卡、时长。"""
    api.post("/api/projects", json={"title": "demo"})
    ep = api.post("/api/projects/demo/episodes", json={}).json()
    sv = api.app.state.services
    with sv.db.session() as s:
        s.add(Export(episode_id=ep["id"], variant="9x16",
                     file_path=sv.media.rel(sv.media.export_path("demo", "ep01_9x16.mp4")),
                     settings={"subtitles": "zh+km", "dub": "km", "bed": False,
                              "align": "timeline", "end_card": True, "duration": 37.1}))
        s.flush()
    [row] = api.get(f"/api/episodes/{ep['id']}/exports").json()
    assert row["label"] == "9:16 · 中高字幕 · 高棉语配音 · 无原声底 · 片尾卡 · 37.1 秒"
    assert row["is_final"] is False


def test_export_label_minimal_settings(api):
    """没有字幕/配音/片尾卡时只剩画幅（没有 duration 就不带秒数）。"""
    api.post("/api/projects", json={"title": "demo"})
    ep = api.post("/api/projects/demo/episodes", json={}).json()
    sv = api.app.state.services
    with sv.db.session() as s:
        s.add(Export(episode_id=ep["id"], variant="16x9",
                     file_path=sv.media.rel(sv.media.export_path("demo", "ep01_16x9.mp4")),
                     settings={"subtitles": "none", "dub": "off", "bed": True,
                              "align": "timeline", "end_card": False}))
        s.flush()
    [row] = api.get(f"/api/episodes/{ep['id']}/exports").json()
    assert row["label"] == "16:9"


def test_patch_export_final_mutual_exclusion_and_list_ordering(api):
    api.post("/api/projects", json={"title": "demo"})
    ep = api.post("/api/projects/demo/episodes", json={}).json()
    sv = api.app.state.services
    with sv.db.session() as s:
        for i in range(2):
            s.add(Export(episode_id=ep["id"], variant="9x16",
                         file_path=sv.media.rel(sv.media.export_path("demo", f"ep01_{i}.mp4")),
                         settings={}))
        s.flush()
    rows = api.get(f"/api/episodes/{ep['id']}/exports").json()
    first_id, second_id = rows[1]["id"], rows[0]["id"]  # id 升序创建，列表默认倒序：rows[0] 是后创建的

    r = api.patch(f"/api/exports/{first_id}", json={"is_final": True})
    assert r.status_code == 200 and r.json()["is_final"] is True

    rows = api.get(f"/api/episodes/{ep['id']}/exports").json()
    assert rows[0]["id"] == first_id and rows[0]["is_final"] is True
    assert rows[1]["id"] == second_id and rows[1]["is_final"] is False

    # 同集只允许一个定稿：定稿第二个后第一个自动取消
    r = api.patch(f"/api/exports/{second_id}", json={"is_final": True})
    assert r.status_code == 200 and r.json()["is_final"] is True
    rows = {x["id"]: x for x in api.get(f"/api/episodes/{ep['id']}/exports").json()}
    assert rows[first_id]["is_final"] is False and rows[second_id]["is_final"] is True

    r = api.patch(f"/api/exports/{second_id}", json={"is_final": False})
    assert r.status_code == 200 and r.json()["is_final"] is False

    assert api.patch("/api/exports/999", json={"is_final": True}).status_code == 404
