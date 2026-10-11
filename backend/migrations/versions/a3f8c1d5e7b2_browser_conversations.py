"""browser conversations

网页会话记录（browser_conversations）：同一个角色 / 镜头 / 场景 / 道具 / 某集某一步在同一个网站上
用过的网页对话地址，重新生成时接着这条对话发。server_default 用普通字符串。

Revision ID: a3f8c1d5e7b2
Revises: d1a7e3c5b9f4
Create Date: 2026-10-11 23:30:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a3f8c1d5e7b2'
down_revision: Union[str, Sequence[str], None] = 'd1a7e3c5b9f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "browser_conversations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("site", sa.String(length=20), nullable=False),
        sa.Column("scope_kind", sa.String(length=20), nullable=False),
        sa.Column("scope_id", sa.String(length=120), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("url", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("conversation_id", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("turns", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default="2026-01-01 00:00:00"),
        sa.Column("updated_at", sa.DateTime(), nullable=False,
                  server_default="2026-01-01 00:00:00"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("site", "scope_kind", "scope_id", name="uq_browser_conv_scope"),
    )
    op.create_index("ix_browser_conversations_project_id", "browser_conversations", ["project_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_browser_conversations_project_id", table_name="browser_conversations")
    op.drop_table("browser_conversations")
