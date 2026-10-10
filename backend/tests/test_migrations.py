"""迁移回归：SQLite 的 batch 重建表会丢掉 AUTOINCREMENT，镜头 id 复用会让新镜头继承旧任务/槽位。"""
import sqlite3

from alembic import command
from alembic.config import Config as AlembicConfig

from backend.domain.db import MIGRATIONS, Database


def _cfg(db: Database) -> AlembicConfig:
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    cfg.set_main_option("sqlalchemy.url", db.url)
    return cfg


def _ddl(path, table):
    c = sqlite3.connect(path)
    try:
        return c.execute("SELECT sql FROM sqlite_master WHERE name=?", (table,)).fetchone()[0]
    finally:
        c.close()


def test_fresh_head_shots_autoincrement(tmp_path):
    d = Database(tmp_path / "m.db")
    d.upgrade()
    d.engine.dispose()
    ddl = _ddl(d.path, "shots")
    assert "AUTOINCREMENT" in ddl
    assert "origin" in ddl and "DEFAULT 'llm'" in ddl
    assert "REFERENCES scene_blocks (id) ON DELETE SET NULL" in ddl
    assert "REFERENCES episodes (id) ON DELETE CASCADE" in ddl


def test_upgrade_restores_shot_sequence_from_refs(tmp_path):
    d = Database(tmp_path / "m.db")
    cfg = _cfg(d)
    # 停在丢了 AUTOINCREMENT 的版本，造数据：镜头最大 id 3，但任务/槽位引用过更大的已删镜头 id
    command.upgrade(cfg, "baedf06b3ae5")
    c = sqlite3.connect(d.path)
    c.execute("INSERT INTO style_bibles (id, palette, lighting, texture, character_rules, "
              "mood_keywords, anchor_text, negative) VALUES (1,'','','','','[]','','')")
    c.execute("INSERT INTO projects (id, slug, title, aspect, image_engine, video_engine, seed, "
              "negative, created_at) VALUES (1,'p','p','9:16','z','w',1,'','2026-01-01')")
    c.execute("INSERT INTO episodes (id, project_id, number, title, source_text, target_seconds, "
              "status, stage_hashes) VALUES (1,1,1,'','',60,'draft','{}')")
    for sid in (1, 3):
        c.execute("INSERT INTO shots (id, episode_id, \"order\", code, shot_size, camera_move, "
                  "action, dialogue, \"cast\", props, image_prompt, motion_prompt, audio_prompt, "
                  "duration, use_end_frame, continue_from_prev, transition_out) VALUES "
                  f"({sid},1,{sid},'','','','','[]','[]','[]','','','',3,0,0,'cut')")
    c.execute("INSERT INTO jobs (type, status, owner_type, owner_id, payload, gpu, priority, "
              "depends_on, log, created_at) VALUES ('shot.video','failed','shot',9,'{}',1,0,'[]',"
              "'','2026-01-01')")
    c.execute("INSERT INTO slots (owner_type, owner_id, kind) VALUES ('shot', 7, 'video')")
    c.commit()
    c.close()
    command.upgrade(cfg, "head")
    d.engine.dispose()
    assert "AUTOINCREMENT" in _ddl(d.path, "shots")
    c = sqlite3.connect(d.path)
    try:
        assert c.execute("SELECT seq FROM sqlite_sequence WHERE name='shots'").fetchone()[0] == 9
        assert [r[0] for r in c.execute("SELECT id FROM shots ORDER BY id")] == [1, 3]
        assert c.execute("SELECT origin FROM shots WHERE id=1").fetchone()[0] == "llm"
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []
        c.execute("INSERT INTO shots (episode_id, \"order\", code, shot_size, camera_move, "
                  "action, dialogue, \"cast\", props, image_prompt, motion_prompt, audio_prompt, "
                  "duration, use_end_frame, continue_from_prev, transition_out) VALUES "
                  "(1,9,'','','','','[]','[]','[]','','','',3,0,0,'cut')")
        assert c.execute("SELECT max(id) FROM shots").fetchone()[0] == 10
    finally:
        c.close()
