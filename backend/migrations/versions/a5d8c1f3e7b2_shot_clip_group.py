"""shot clip_group

固定时长视频引擎第 2 步「组镜拆分」（设计文档 docs/specs/2026-10-12-fixed-length-engines-design.md §3.3）：

- shots.clip_group：同一段落里相连、组号相同的几个镜头一次生成（视频候选挂在组里第一个镜头「主镜」上，
  导出时按偏移拆给各个成员）；空 = 不在组镜里，老镜头不变。

shots 用 op.add_column（SQLite 的 ALTER TABLE ADD COLUMN）：batch 重建 shots 表会丢掉 AUTOINCREMENT
（见 c8a4f1e2d6b9）。

Revision ID: a5d8c1f3e7b2
Revises: f4a7c2e9b3d1
Create Date: 2026-10-12 23:00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a5d8c1f3e7b2'
down_revision: Union[str, Sequence[str], None] = 'f4a7c2e9b3d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("shots", sa.Column("clip_group", sa.Integer(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    # shots 不走 batch 重建（会丢 AUTOINCREMENT）：SQLite 3.35+ 支持 DROP COLUMN
    op.drop_column("shots", "clip_group")
