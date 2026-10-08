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
