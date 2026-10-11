"""project aigc

项目级 AI 生成内容标识设置：画面角标文字（默认「本片由 AI 辅助制作」）、出品方（写进成片元数据）。
广电总局微短剧 AI 标识要求、《人工智能生成合成内容标识办法》的显式 / 隐式标识。

Revision ID: e3b9d7a1c5f8
Revises: b9e3d7a1c5f2
Create Date: 2026-10-12 02:00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'e3b9d7a1c5f8'
down_revision: Union[str, Sequence[str], None] = 'b9e3d7a1c5f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("projects") as batch:
        batch.add_column(sa.Column("aigc", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("projects") as batch:
        batch.drop_column("aigc")
