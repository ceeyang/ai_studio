from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import select

from backend.domain.db import Database
from backend.domain.models import Base, Candidate, Episode, Job, Project, SceneBlock, Shot, Slot


def test_shot_json_fields_roundtrip(db):
    with db.session() as s:
        p = Project(slug="提词员", title="提词员")
        s.add(p)
        s.flush()
        ep = Episode(project_id=p.id, number=1, source_text="梗概")
        s.add(ep)
        s.flush()
        s.add(Shot(episode_id=ep.id, order=0, code="S01",
                   dialogue=[{"speaker": "林", "zh": "你好", "start": 0.5, "end": 1.5}],
                   cast=[{"character_id": 1, "variation_id": None}]))
    with db.session() as s:
        shot = s.scalar(select(Shot))
        assert shot.dialogue[0]["zh"] == "你好"
        assert shot.transition_out == "hard_cut"
        assert shot.duration == 5.0
        ep = s.scalar(select(Episode))
        assert ep.stage_hashes == {}
        assert [x.code for x in ep.shots] == ["S01"]


def test_delete_project_cascades(db):
    with db.session() as s:
        p = Project(slug="demo", title="Demo")
        s.add(p)
        s.flush()
        ep = Episode(project_id=p.id, number=1)
        s.add(ep)
        s.flush()
        s.add(SceneBlock(episode_id=ep.id, order=0, text="段落"))
        s.add(Shot(episode_id=ep.id, order=0, code="S01"))
    with db.session() as s:
        s.delete(s.scalar(select(Project)))
    with db.session() as s:
        assert s.scalar(select(Shot)) is None
        assert s.scalar(select(SceneBlock)) is None


def test_job_defaults(db):
    with db.session() as s:
        j = Job(type="x")
        s.add(j)
        s.flush()
        assert j.status == "queued"
        assert j.payload == {} and j.depends_on == [] and j.log == ""


def test_slot_candidates_ordered(db):
    with db.session() as s:
        slot = Slot(owner_type="shot", owner_id=1, kind="first_frame")
        s.add(slot)
        s.flush()
        for i in range(3):
            s.add(Candidate(slot_id=slot.id, file_path=f"a/{i}.png", engine="zimage"))
    with db.session() as s:
        slot = s.scalar(select(Slot))
        assert [c.file_path for c in slot.candidates] == ["a/0.png", "a/1.png", "a/2.png"]


def test_migrations_match_models(tmp_path):
    d = Database(tmp_path / "m.db")
    d.upgrade()
    with d.engine.connect() as conn:
        mc = MigrationContext.configure(conn, opts={"render_as_batch": True})
        diff = compare_metadata(mc, Base.metadata)
    d.engine.dispose()
    assert diff == []
