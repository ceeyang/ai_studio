"""项目概览：项目中心卡片用的封面、剧集数、每集镜头数 / 成片数，一次请求拿全。"""
from backend.domain.models import Export, Shot
from backend.domain.slots import add_candidate, get_or_create_slot


def _shot(s, ep_id, order):
    sh = Shot(episode_id=ep_id, order=order, code=f"S{order + 1:02d}")
    s.add(sh)
    s.flush()
    return sh.id


def _frame(s, shot_id, name, *, kind="first_frame"):
    return add_candidate(s, get_or_create_slot(s, "shot", shot_id, kind),
                         file_path=f"提词员/media/{kind}/{name}.png", engine="zimage", params={},
                         input_hash=name, job_id=None)


def test_overview_empty_project(api):
    api.post("/api/projects", json={"title": "demo"})
    r = api.get("/api/projects/demo/overview")
    assert r.status_code == 200, r.text
    assert r.json() == {"episode_count": 0, "cover_url": None, "episodes": []}
    assert api.get("/api/projects/nope/overview").status_code == 404


def test_overview_counts_and_covers(api):
    api.post("/api/projects", json={"title": "提词员"})
    e1 = api.post("/api/projects/提词员/episodes", json={"source_text": "x"}).json()
    e2 = api.post("/api/projects/提词员/episodes", json={"source_text": "y"}).json()
    e3 = api.post("/api/projects/提词员/episodes", json={"source_text": "z"}).json()
    db = api.app.state.services.db
    with db.session() as s:
        # 第 1 集：S01 只有视频没有首帧，S02 有首帧 → 封面取 S02 选定的首帧
        a = _shot(s, e1["id"], 0)
        b = _shot(s, e1["id"], 1)
        _frame(s, a, "v1", kind="video")
        _frame(s, b, "f1")
        s.add(Export(episode_id=e1["id"], variant="9x16", file_path="提词员/exports/a.mp4"))
        s.add(Export(episode_id=e1["id"], variant="9x16", file_path="提词员/exports/b.mp4"))
        # 第 2 集：有首帧（项目封面取最新一集里有封面的）
        c = _shot(s, e2["id"], 0)
        _frame(s, c, "f2")
        # 第 3 集：镜头还没生成任何图
        _shot(s, e3["id"], 0)
    body = api.get("/api/projects/提词员/overview").json()
    assert body["episode_count"] == 3
    rows = {e["id"]: e for e in body["episodes"]}
    assert [e["id"] for e in body["episodes"]] == [e1["id"], e2["id"], e3["id"]]
    assert rows[e1["id"]]["shot_count"] == 2 and rows[e1["id"]]["export_count"] == 2
    assert rows[e1["id"]]["cover_url"] == "/media/%E6%8F%90%E8%AF%8D%E5%91%98/media/first_frame/f1.png"
    assert rows[e2["id"]]["cover_url"].endswith("/first_frame/f2.png")
    assert rows[e3["id"]] == {"id": e3["id"], "number": 3, "title": "", "status": "draft",
                              "shot_count": 1, "export_count": 0, "cover_url": None}
    assert body["cover_url"] == rows[e2["id"]]["cover_url"]
