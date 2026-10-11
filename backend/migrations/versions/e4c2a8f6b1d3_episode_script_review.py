"""episode script review

episodes.script_review：剧本体检（script.review）的 LLM 打分、改写建议和当时的输入指纹（JSON，可空）。
规则检查不存，每次按分镜现算。只加一列，用 op.add_column（SQLite 的 ALTER TABLE ADD COLUMN）。

Revision ID: e4c2a8f6b1d3
Revises: a3f8c1d5e7b2
Create Date: 2026-10-12 08:00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'e4c2a8f6b1d3'
down_revision: Union[str, Sequence[str], None] = 'a3f8c1d5e7b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("episodes", sa.Column("script_review", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    # 不走 batch 重建（episodes 被镜头、段落、成片的外键引用）：SQLite 3.35+ 支持 DROP COLUMN
    op.drop_column("episodes", "script_review")
