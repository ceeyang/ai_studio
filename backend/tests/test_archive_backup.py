"""重跑分镜删镜头前：自动备份数据库、删掉的镜头的候选进「历史候选」，可以恢复到别的镜头上。"""
import sqlite3

from sqlalchemy import func, select

from backend.domain import backup as backup_mod
from backend.domain.backup import BACKUP_KEEP, backup_database
from backend.domain.models import ArchivedCandidate, Candidate, Episode, Shot, Slot
from backend.domain.slots import add_candidate, get_or_create_slot
from backend.pipeline.storyboard import run_storyboard
from backend.tests.test_storyboard import setup, shots_json


# ------------------------------------------------------------ 备份
def test_backup_copies_db_and_keeps_newest(db, tmp_path):
    paths = []
    for i in range(BACKUP_KEEP + 3):
        p = backup_database(db, "重跑 分镜/x", now=f"20260101-0000{i:02d}")
        assert p is not None
        paths.append(p)
    root = tmp_path / "backups"
    left = sorted(x.name for x in root.glob("studio-*.db"))
    assert len(left) == BACKUP_KEEP
    assert left == sorted(p.name for p in paths[-BACKUP_KEEP:])
    assert paths[-1].name.startswith("studio-20260101-000012-")
    assert "/" not in paths[-1].name and " " not in paths[-1].name
    c = sqlite3.connect(paths[-1])
    try:
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        c.close()
    assert "shots" in tables and "archived_candidates" in tables


def test_backup_failure_returns_none(db, monkeypatch, caplog):
    def boom(*a, **k):
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr(backup_mod.sqlite3, "connect", boom)
    assert backup_database(db, "storyboard") is None
    assert "disk full" in caplog.text


# ------------------------------------------------------------ 分镜重跑
def _with_candidates(db, ep_id):
    """第二段的两个镜头各挂一个首帧（选定）和一个视频（选定）+ 一个没选的视频。"""
    with db.session() as s:
        ep = s.get(Episode, ep_id)
        b2 = ep.scene_blocks[1].id
        old = ep.shots[2:]
        old[0].image_prompt = "hand edited prompt"
        for sh in old:
            add_candidate(s, get_or_create_slot(s, "shot", sh.id, "first_frame"),
                          file_path=f"demo/ff/{sh.id}.png", engine="zimage", params={"seed": 1},
                          input_hash="h", job_id=None)
            v = get_or_create_slot(s, "shot", sh.id, "video")
            add_candidate(s, v, file_path=f"demo/v/{sh.id}a.mp4", engine="h3", params={},
                          input_hash="h", job_id=None)
            add_candidate(s, v, file_path=f"demo/v/{sh.id}b.mp4", engine="h3", params={},
                          input_hash="h", job_id=None, auto_select=False)
        return b2, [(x.id, x.code) for x in old]


def test_rerun_archives_candidates_and_backs_up(tmp_path, db):
    ctx, ep_id, _ = setup(tmp_path, db, [shots_json(2), shots_json(2), shots_json(2)])
    run_storyboard(ctx, {"episode_id": ep_id})
    assert not (tmp_path / "backups").exists()   # 第一次出分镜没有东西要删：不备份
    b2, old = _with_candidates(db, ep_id)
    ctx.logs.clear()
    run_storyboard(ctx, {"episode_id": ep_id, "scene_block_ids": [b2], "force": True})

    backups = list((tmp_path / "backups").glob("studio-*.db"))
    assert len(backups) == 1
    assert any("已自动备份数据库" in x and backups[0].name in x and "保留最近 10 份" in x
               for x in ctx.logs)
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(Candidate)) == 0
        assert s.scalar(select(func.count()).select_from(Slot)) == 0
        rows = list(s.scalars(select(ArchivedCandidate).order_by(ArchivedCandidate.id)))
        assert len(rows) == 6
        assert {r.episode_id for r in rows} == {ep_id}
        assert {r.shot_code for r in rows} == {code for _, code in old}
        assert sorted(r.kind for r in rows) == ["first_frame"] * 2 + ["video"] * 4
        sel = {r.file_path for r in rows if r.was_selected}
        assert sel == {f"demo/ff/{i}.png" for i, _ in old} | {f"demo/v/{i}a.mp4" for i, _ in old}
        ff = next(r for r in rows if r.file_path == f"demo/ff/{old[0][0]}.png")
        assert ff.engine == "zimage" and ff.params == {"seed": 1}
        assert ff.shot_snapshot["image_prompt"] == "hand edited prompt"
        assert ff.reason == "storyboard"
        assert ff.archived_at is not None


def test_unchanged_rerun_does_not_backup(tmp_path, db):
    ctx, ep_id, _ = setup(tmp_path, db, [shots_json(2), shots_json(2)])
    run_storyboard(ctx, {"episode_id": ep_id})
    run_storyboard(ctx, {"episode_id": ep_id})   # 输入没变：两段都跳过
    assert not (tmp_path / "backups").exists()


def test_backup_failure_does_not_fail_job(tmp_path, db, monkeypatch):
    ctx, ep_id, _ = setup(tmp_path, db, [shots_json(2), shots_json(2), shots_json(2)])
    run_storyboard(ctx, {"episode_id": ep_id})
    b2, _ = _with_candidates(db, ep_id)

    def boom(*a, **k):
        raise OSError("no space")

    monkeypatch.setattr(backup_mod.sqlite3, "connect", boom)
    ctx.logs.clear()
    run_storyboard(ctx, {"episode_id": ep_id, "scene_block_ids": [b2], "force": True})
    assert any("自动备份数据库失败" in x for x in ctx.logs)
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(ArchivedCandidate)) == 6


def test_backup_once_per_job(tmp_path, db):
    ctx, ep_id, _ = setup(tmp_path, db, [shots_json(2)] * 4)
    run_storyboard(ctx, {"episode_id": ep_id})
    run_storyboard(ctx, {"episode_id": ep_id, "force": True})   # 两段都删：只备份一次
    assert len(list((tmp_path / "backups").glob("studio-*.db"))) == 1


# ------------------------------------------------------------ API
def _api_seed(api):
    """借用 API 的数据库：造一集、一个镜头、两条历史候选。"""
    from datetime import datetime

    from backend.domain.models import Project

    db = api.app.state.services.db
    with db.session() as s:
        p = Project(slug="demo", title="t", image_engine="zimage", video_engine="h3_video")
        s.add(p)
        s.flush()
        ep = Episode(project_id=p.id, number=1)
        s.add(ep)
        s.flush()
        shot = Shot(episode_id=ep.id, order=0, code="S01")
        s.add(shot)
        s.flush()
        a = ArchivedCandidate(project_id=p.id, episode_id=ep.id, shot_code="S03",
                              kind="video", file_path="demo/v/old.mp4", engine="h3",
                              params={"x": 1}, input_hash="h", was_selected=True,
                              shot_snapshot={"action": "旧动作"}, reason="storyboard",
                              created_at=datetime(2026, 1, 1),
                              archived_at=datetime(2026, 1, 2))
        b = ArchivedCandidate(project_id=p.id, episode_id=ep.id, shot_code="S04",
                              kind="first_frame", file_path="demo/ff/old.png", engine="zimage",
                              params={}, input_hash="h", was_selected=False, shot_snapshot={},
                              reason="storyboard", created_at=datetime(2026, 1, 1),
                              archived_at=datetime(2026, 1, 3))
        s.add_all([a, b])
        s.flush()
        return db, ep.id, shot.id, a.id, b.id


def test_api_list_restore_delete(api):
    db, ep_id, shot_id, a_id, b_id = _api_seed(api)
    r = api.get(f"/api/episodes/{ep_id}/archived-candidates")
    assert r.status_code == 200
    rows = r.json()
    assert [x["id"] for x in rows] == [b_id, a_id]   # 新归档的在前
    assert rows[1]["url"] == "/media/demo/v/old.mp4"
    assert rows[1]["shot_code"] == "S03" and rows[1]["was_selected"] is True
    assert rows[1]["shot_snapshot"] == {"action": "旧动作"}
    assert api.get(f"/api/episodes/{ep_id}/archived-candidates?kind=video").json()[0]["id"] == a_id

    r = api.post(f"/api/archived-candidates/{a_id}/restore", json={"shot_id": shot_id})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["file_path"] == "demo/v/old.mp4" and body["engine"] == "h3"
    with db.session() as s:
        slot = s.scalar(select(Slot).where(Slot.owner_type == "shot", Slot.owner_id == shot_id,
                                           Slot.kind == "video"))
        assert slot.selected_candidate_id == body["id"]   # 槽位还没有选定：恢复的就是选定
        assert s.get(ArchivedCandidate, a_id) is None      # 恢复 = 挪回去，历史里不再留

    # 槽位已有选定：恢复的只加进候选，不抢选定
    db2 = db
    with db2.session() as s:
        s.add(ArchivedCandidate(project_id=1, episode_id=ep_id, shot_code="S05", kind="video",
                                file_path="demo/v/old2.mp4", engine="h3", params={},
                                input_hash="", was_selected=True, shot_snapshot={},
                                reason="storyboard"))
    third = api.get(f"/api/episodes/{ep_id}/archived-candidates?kind=video").json()[0]["id"]
    r = api.post(f"/api/archived-candidates/{third}/restore", json={"shot_id": shot_id})
    assert r.status_code == 200
    with db.session() as s:
        slot = s.scalar(select(Slot).where(Slot.owner_type == "shot", Slot.owner_id == shot_id,
                                           Slot.kind == "video"))
        assert slot.selected_candidate_id == body["id"]
        assert len(slot.candidates) == 2

    assert api.delete(f"/api/archived-candidates/{b_id}").status_code == 204
    assert api.get(f"/api/episodes/{ep_id}/archived-candidates").json() == []
    assert api.post(f"/api/archived-candidates/{b_id}/restore",
                    json={"shot_id": shot_id}).status_code == 404


def test_api_restore_shot_ref_is_not_selected_and_respects_limit(api):
    """构图参考图（kind=shot_ref）恢复回构图参考图槽位：不选定（槽位里的图全部都用），每镜最多 2 张。"""
    db, ep_id, shot_id, _, _ = _api_seed(api)
    with db.session() as s:
        for i in range(3):
            s.add(ArchivedCandidate(project_id=1, episode_id=ep_id, shot_code="S02", kind="shot_ref",
                                    file_path=f"demo/shot_ref/{i}.png", engine="upload", params={},
                                    input_hash="", was_selected=False, shot_snapshot={},
                                    reason="storyboard"))
    ids = [x["id"] for x in api.get(f"/api/episodes/{ep_id}/archived-candidates?kind=shot_ref").json()]
    assert len(ids) == 3
    for i in ids[:2]:
        assert api.post(f"/api/archived-candidates/{i}/restore", json={"shot_id": shot_id}).status_code == 200
    r = api.post(f"/api/archived-candidates/{ids[2]}/restore", json={"shot_id": shot_id})
    assert r.status_code in (400, 409, 422) and "构图参考图" in r.text
    with db.session() as s:
        slot = s.scalar(select(Slot).where(Slot.owner_type == "shot", Slot.owner_id == shot_id,
                                           Slot.kind == "shot_ref"))
        assert slot.selected_candidate_id is None and len(slot.candidates) == 2


def test_api_restore_rejects_shot_of_other_episode(api):
    db, ep_id, shot_id, a_id, _ = _api_seed(api)
    with db.session() as s:
        other = Episode(project_id=1, number=2)
        s.add(other)
        s.flush()
        sh = Shot(episode_id=other.id, order=0, code="S01")
        s.add(sh)
        s.flush()
        other_shot = sh.id
    r = api.post(f"/api/archived-candidates/{a_id}/restore", json={"shot_id": other_shot})
    assert r.status_code in (400, 409, 422)
    assert api.post(f"/api/archived-candidates/{a_id}/restore",
                    json={"shot_id": 9999}).status_code == 404
