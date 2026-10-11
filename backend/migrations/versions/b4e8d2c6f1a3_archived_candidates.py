"""archived candidates

历史候选：重跑分镜删掉镜头时，它的首帧/视频候选挪到这张表（带镜头快照），界面上可以恢复。

Revision ID: b4e8d2c6f1a3
Revises: a9c3e5f7b2d4
Create Date: 2026-10-11 12:00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'b4e8d2c6f1a3'
down_revision: Union[str, Sequence[str], None] = 'a9c3e5f7b2d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "archived_candidates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("episode_id", sa.Integer(), nullable=False),
        sa.Column("shot_code", sa.String(length=16), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("file_path", sa.String(length=500), nullable=False),
        sa.Column("engine", sa.String(length=40), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("was_selected", sa.Boolean(), nullable=False),
        sa.Column("shot_snapshot", sa.JSON(), nullable=False),
        sa.Column("reason", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("archived_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["episode_id"], ["episodes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_archived_candidates_episode_id", "archived_candidates", ["episode_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_archived_candidates_episode_id", table_name="archived_candidates")
    op.drop_table("archived_candidates")
