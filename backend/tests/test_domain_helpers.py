import pytest
from sqlalchemy import select

from backend.domain.hashing import input_hash
from backend.domain.media import MediaStore
from backend.domain.models import Episode, Project, Shot
from backend.domain.shots import renumber_shots
from backend.domain.slots import (add_candidate, get_or_create_slot, select_candidate,
                                  selected_candidate, slots_for)


def test_media_paths_with_chinese_slug(tmp_path):
    m = MediaStore(tmp_path)
    p = m.new_media_path("提词员", "first_frame", ".PNG")
    assert p.parent == (tmp_path / "提词员" / "media" / "first_frame").resolve()
    assert p.suffix == ".png" and p.parent.is_dir()
    rel = m.rel(p)
    assert rel.startswith("提词员/media/first_frame/")
    assert m.abs(rel) == p
    assert m.run_dir("提词员", 7).is_dir()


def test_media_rejects_traversal_and_private_files(tmp_path):
    m = MediaStore(tmp_path)
    with pytest.raises(ValueError):
        m.abs("../outside.txt")
    (tmp_path / "studio.db").write_text("x")
    with pytest.raises(PermissionError):
        m.public_file("studio.db")
    with pytest.raises(PermissionError):
        m.public_file("demo/runs/1/workflow.api.json")
    f = m.new_media_path("demo", "video", "mp4")
    f.write_bytes(b"v")
    assert m.public_file(m.rel(f)) == f


def test_input_hash_stable_and_sensitive():
    a = input_hash("kf", "prompt", engine="zimage", size=[768, 1344])
    assert a == input_hash("kf", "prompt", size=[768, 1344], engine="zimage")
    assert a != input_hash("kf", "prompt2", engine="zimage", size=[768, 1344])
    assert len(a) == 16


def test_candidates_auto_select_rules(db):
    with db.session() as s:
        slot = get_or_create_slot(s, "shot", 1, "first_frame")
        c1 = add_candidate(s, slot, file_path="a.png", engine="zimage", params={},
                           input_hash="h1", job_id=None)
        assert slot.selected_candidate_id == c1.id          # 空槽位：选新的
        c2 = add_candidate(s, slot, file_path="b.png", engine="zimage", params={},
                           input_hash="h1", job_id=None)
        assert slot.selected_candidate_id == c1.id          # 同输入：保留人工选择
        c3 = add_candidate(s, slot, file_path="c.png", engine="zimage", params={},
                           input_hash="h2", job_id=None)
        assert slot.selected_candidate_id == c3.id          # 旧的已过期：选新的
        assert get_or_create_slot(s, "shot", 1, "first_frame").id == slot.id
    with db.session() as s:
        select_candidate(s, c2.id)
    with db.session() as s:
        assert selected_candidate(s, "shot", 1, "first_frame").id == c2.id
        assert selected_candidate(s, "shot", 1, "video") is None
        assert [x.kind for x in slots_for(s, "shot", 1)] == ["first_frame"]
        with pytest.raises(LookupError):
            select_candidate(s, 999)


def _episode_with_shots(db, n):
    with db.session() as s:
        p = Project(slug="demo", title="Demo")
        s.add(p)
        s.flush()
        ep = Episode(project_id=p.id, number=1)
        s.add(ep)
        s.flush()
        ids = []
        for i in range(n):
            sh = Shot(episode_id=ep.id, order=i * 10, code="")
            s.add(sh)
            s.flush()
            ids.append(sh.id)
        return ep.id, ids


def test_renumber_compacts_and_reorders(db):
    ep_id, ids = _episode_with_shots(db, 3)
    with db.session() as s:
        renumber_shots(s, ep_id)
    with db.session() as s:
        shots = s.scalars(select(Shot).order_by(Shot.order)).all()
        assert [(x.order, x.code) for x in shots] == [(0, "S01"), (1, "S02"), (2, "S03")]
    with db.session() as s:
        renumber_shots(s, ep_id, [ids[2], ids[0], ids[1]])
    with db.session() as s:
        assert [x.id for x in s.scalars(select(Shot).order_by(Shot.order))] == [ids[2], ids[0], ids[1]]
        with pytest.raises(ValueError):
            renumber_shots(s, ep_id, [ids[0], ids[1]])
