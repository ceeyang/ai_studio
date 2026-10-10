"""shots autoincrement again

fce408674655（加 origin 列，带表达式默认值）让 SQLite 的 batch 重建了 shots 表，
反射拿不到 sqlite_autoincrement，表重建后丢了 AUTOINCREMENT，sqlite_sequence 里的记录也没了：
删掉最新的镜头/剧集、重跑最后一个分镜块后，新镜头会复用旧 id，继承旧任务的失败、上下文和槽位。

这里再按 AUTOINCREMENT 重建一次（列、外键、默认值、数据都保留），
并把序号设成「现有镜头、任务、槽位、提示词版本里出现过的最大镜头 id」，避免复用已删镜头的 id。
（projects / exports 本来就不是 AUTOINCREMENT，baedf06b3ae5 的重建没有丢东西。）

Revision ID: c8a4f1e2d6b9
Revises: baedf06b3ae5
Create Date: 2026-10-10 22:00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'c8a4f1e2d6b9'
down_revision: Union[str, Sequence[str], None] = 'baedf06b3ae5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_MAX_SHOT_ID = sa.text(
    "SELECT max(m) FROM ("
    " SELECT max(id) AS m FROM shots"
    " UNION ALL SELECT max(owner_id) FROM jobs WHERE owner_type = 'shot'"
    " UNION ALL SELECT max(owner_id) FROM slots WHERE owner_type = 'shot'"
    " UNION ALL SELECT max(owner_id) FROM prompt_versions WHERE owner_type = 'shot')")


def _old_seq(bind) -> int:
    has = bind.execute(sa.text(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sqlite_sequence'")).scalar()
    if not has:
        return 0
    return bind.execute(sa.text(
        "SELECT seq FROM sqlite_sequence WHERE name = 'shots'")).scalar() or 0


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    old = _old_seq(bind)
    with op.batch_alter_table("shots", recreate="always",
                              table_kwargs={"sqlite_autoincrement": True}):
        pass
    seq = max(old, bind.execute(_MAX_SHOT_ID).scalar() or 0)
    bind.execute(sa.text("DELETE FROM sqlite_sequence WHERE name = 'shots'"))
    if seq:
        bind.execute(sa.text("INSERT INTO sqlite_sequence (name, seq) VALUES ('shots', :s)"),
                     {"s": seq})


def downgrade() -> None:
    """Downgrade schema."""
    # 回到上一版时保持 AUTOINCREMENT 不动：去掉它只会重新引入 id 复用
    pass
