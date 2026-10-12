"""shot beats + project fixed_engine_pack

固定时长视频引擎（设计文档 docs/specs/2026-10-12-fixed-length-engines-design.md 第 1 步）：

- shots.beats：多拍镜头的每一拍 [{start, end, shot_size, camera_move, action}]（2–4 拍，首尾相接、覆盖整个
  镜头时长）；空 = 普通镜头，老镜头不变。
- projects.fixed_engine_pack：固定时长视频引擎（Gemini 固定 10 秒、Muse 只认 3 / 5 / 10 秒）怎么用满一次生成：
  beats（多拍镜头，默认）/ group（组镜拆分，第 2 步实现，现在按 off 处理）/ off（老行为：一镜一生成）。

shots 用 op.add_column（SQLite 的 ALTER TABLE ADD COLUMN）：batch 重建 shots 表会丢掉 AUTOINCREMENT
（见 c8a4f1e2d6b9）。

Revision ID: f4a7c2e9b3d1
Revises: e3b9d7a1c5f8
Create Date: 2026-10-12 20:00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'f4a7c2e9b3d1'
down_revision: Union[str, Sequence[str], None] = 'e3b9d7a1c5f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("shots", sa.Column("beats", sa.JSON(), nullable=True))
    with op.batch_alter_table("projects") as batch:
        batch.add_column(sa.Column("fixed_engine_pack", sa.String(length=16), nullable=False,
                                   server_default="beats"))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("projects") as batch:
        batch.drop_column("fixed_engine_pack")
    # shots 不走 batch 重建（会丢 AUTOINCREMENT）：SQLite 3.35+ 支持 DROP COLUMN
    op.drop_column("shots", "beats")
