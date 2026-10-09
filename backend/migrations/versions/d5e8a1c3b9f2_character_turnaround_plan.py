"""character turnaround plan

角色三视图 9 格的视角描述（character.turnaround_plan 的结果，用户可改）：{view_key: description}。

Revision ID: d5e8a1c3b9f2
Revises: b7d2e4f6a8c1
Create Date: 2026-10-09 23:30:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'd5e8a1c3b9f2'
down_revision: Union[str, Sequence[str], None] = 'b7d2e4f6a8c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("characters") as batch:
        batch.add_column(sa.Column("turnaround_plan", sa.JSON(), nullable=False,
                                   server_default=sa.text("'{}'")))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("characters") as batch:
        batch.drop_column("turnaround_plan")
