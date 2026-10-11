"""shot video mode

- shots.video_mode：这一镜的视频怎么出——first_frame（先出首帧，图生视频）或 refs（参考图直出：不出首帧，
  用场景图、构图参考、定妆照 / 三视图、道具图直接出视频）。空 = first_frame，老镜头不变。

用 op.add_column（SQLite 的 ALTER TABLE ADD COLUMN）：batch 重建 shots 表会丢掉 AUTOINCREMENT
（见 c8a4f1e2d6b9）。

Revision ID: b9e3d7a1c5f2
Revises: a3f8c1d5e7b2
Create Date: 2026-10-12 12:00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'b9e3d7a1c5f2'
down_revision: Union[str, Sequence[str], None] = 'a3f8c1d5e7b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("shots", sa.Column("video_mode", sa.String(length=16), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    # shots 不走 batch 重建（会丢 AUTOINCREMENT）：SQLite 3.35+ 支持 DROP COLUMN
    op.drop_column("shots", "video_mode")
