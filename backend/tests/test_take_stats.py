"""抽卡统计：backend/domain/take_stats.py 和 GET /api/episodes/{id}/take-stats。"""
from __future__ import annotations

from backend.domain.models import ArchivedCandidate, Candidate, Job, JobTrace, Shot, Slot
from backend.domain.take_stats import episode_take_stats
from backend.tests.helpers import seed_episode


def _slot(s, shot_id, kind, engines, select=None):
    """给镜头的槽位加候选（engines 依次一条），select 是选第几条（从 1 开始）。"""
    slot = Slot(owner_type="shot", owner_id=shot_id, kind=kind)
    s.add(slot)
    s.flush()
    cands = []
    for i, e in enumerate(engines):
        c = Candidate(slot_id=slot.id, file_path=f"x/{shot_id}_{kind}_{i}", engine=e, params={})
        s.add(c)
        s.flush()
        cands.append(c)
    if select:
        slot.selected_candidate_id = cands[select - 1].id
    return cands


def _failed(s, shot_id, jtype, engine=None, status="failed"):
    j = Job(type=jtype, status=status, owner_type="shot", owner_id=shot_id, error="boom")
    s.add(j)
    s.flush()
    if engine:
        s.add(JobTrace(job_id=j.id, seq=1, kind="engine", title="t", data={"engine": engine}))
    return j


def _seed(db):
    pid, ep_id = seed_episode(db, image_engine="zimage", video_engine="h3_video")
    with db.session() as s:
        a = Shot(episode_id=ep_id, order=0, code="S01")
        b = Shot(episode_id=ep_id, order=1, code="S02", video_engine="kling_video")
        c = Shot(episode_id=ep_id, order=2, code="S03")
        s.add_all([a, b, c])
        s.flush()
        # S01：首帧 3 抽选第 2 条；视频 5 抽（两个引擎）选第 4 条，失败 1 次（有调用明细）
        _slot(s, a.id, "first_frame", ["zimage", "zimage", "h3_image"], select=2)
        _slot(s, a.id, "video", ["h3_video"] * 3 + ["kling_video"] * 2, select=4)
        _failed(s, a.id, "video.shot", engine="kling_video")
        # S02：首帧 1 抽选第 1 条；视频 2 抽没选；首帧失败 1 次、视频失败 1 次（没有明细：按镜头 / 项目引擎）
        _slot(s, b.id, "first_frame", ["zimage"], select=1)
        _slot(s, b.id, "video", ["h3_video", "h3_video"])
        _failed(s, b.id, "image.keyframe")
        _failed(s, b.id, "video.shot")
        # 取消的、别的类型的任务不算失败
        _failed(s, b.id, "video.shot", status="canceled")
        _failed(s, b.id, "export.episode")
        # 废弃镜头的抽卡
        s.add_all([ArchivedCandidate(project_id=pid, episode_id=ep_id, kind="video", file_path="o/1"),
                   ArchivedCandidate(project_id=pid, episode_id=ep_id, kind="video", file_path="o/2"),
                   ArchivedCandidate(project_id=pid, episode_id=ep_id, kind="first_frame",
                                     file_path="o/3")])
        return ep_id, a.id, b.id, c.id


def test_episode_take_stats(db):
    ep_id, a, b, c = _seed(db)
    with db.session() as s:
        st = episode_take_stats(s, ep_id)
    shots = {x.shot_id: x for x in st.shots}
    assert [x.code for x in st.shots] == ["S01", "S02", "S03"]
    sa, sb, sc = shots[a], shots[b], shots[c]
    assert (sa.first_frame.takes, sa.first_frame.failed, sa.first_frame.selected_index) == (3, 0, 2)
    assert sa.first_frame.engines == {"zimage": 2, "h3_image": 1}
    assert (sa.video.takes, sa.video.failed, sa.video.selected_index) == (5, 1, 4)
    assert sa.video.engines == {"h3_video": 3, "kling_video": 2}
    assert (sb.first_frame.takes, sb.first_frame.failed, sb.first_frame.selected_index) == (1, 1, 1)
    assert (sb.video.takes, sb.video.failed, sb.video.selected_index) == (2, 1, None)
    assert (sc.first_frame.takes, sc.video.takes, sc.video.selected_index) == (0, 0, None)

    assert (st.total_takes, st.failed) == (11, 3)
    assert (st.first_frame_takes, st.video_takes, st.shots_with_video) == (4, 7, 1)
    assert st.usable_rate == round(1 / 7, 4)
    # 出完的只有 S01：3 + 5 + 失败 1
    assert st.avg_takes_per_finished_shot == 9.0
    eng = {(e.kind, e.engine): (e.takes, e.failed) for e in st.engines}
    assert eng == {("first_frame", "zimage"): (3, 1), ("first_frame", "h3_image"): (1, 0),
                   ("video", "h3_video"): (5, 0), ("video", "kling_video"): (2, 2)}
    assert [e.kind for e in st.engines] == ["first_frame"] * 2 + ["video"] * 2
    assert st.archived_takes == {"video": 2, "first_frame": 1}


def test_empty_episode(db):
    _, ep_id = seed_episode(db)
    with db.session() as s:
        st = episode_take_stats(s, ep_id)
    assert st.shots == [] and st.total_takes == 0
    assert st.usable_rate is None and st.avg_takes_per_finished_shot is None
    assert st.engines == [] and st.archived_takes == {}


def test_take_stats_route(api):
    api.post("/api/projects", json={"title": "demo", "image_engine": "zimage"})
    ep = api.post("/api/projects/demo/episodes", json={"source_text": "x"}).json()
    db = api.app.state.services.db
    with db.session() as s:
        sh = Shot(episode_id=ep["id"], order=0, code="S01")
        s.add(sh)
        s.flush()
        _slot(s, sh.id, "video", ["h3_video", "h3_video"], select=2)
        _failed(s, sh.id, "video.shot", engine="h3_video")
    r = api.get(f"/api/episodes/{ep['id']}/take-stats")
    assert r.status_code == 200
    body = r.json()
    assert body["shots"][0]["video"] == {"takes": 2, "failed": 1, "selected_index": 2,
                                         "engines": {"h3_video": 2}}
    assert body["usable_rate"] == 0.5 and body["avg_takes_per_finished_shot"] == 3.0
    assert body["engines"] == [{"engine": "h3_video", "kind": "video", "takes": 2, "failed": 1}]
    assert api.get("/api/episodes/9999/take-stats").status_code == 404
