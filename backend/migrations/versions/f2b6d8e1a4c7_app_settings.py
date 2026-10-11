"""app settings

全局设置表：默认文本模型、默认图片/视频来源、按步骤单独设置（设置页「默认模型」）。

Revision ID: f2b6d8e1a4c7
Revises: c8a4f1e2d6b9
Create Date: 2026-10-11 10:00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'f2b6d8e1a4c7'
down_revision: Union[str, Sequence[str], None] = 'c8a4f1e2d6b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(length=80), nullable=False),
        sa.Column("value", sa.JSON(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False,
                  server_default="2026-01-01 00:00:00"),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("app_settings")
