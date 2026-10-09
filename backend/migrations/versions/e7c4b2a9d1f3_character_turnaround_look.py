"""character turnaround look

三视图改成一张设定图一起出（版式是固定模板），不再逐格写视角描述：去掉 turnaround_plan，
加 turnaround_look（出设定图用的从头到脚的全身描述，可改）。旧的逐格槽位和媒体文件不动。

Revision ID: e7c4b2a9d1f3
Revises: d5e8a1c3b9f2
Create Date: 2026-10-10 10:00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'e7c4b2a9d1f3'
down_revision: Union[str, Sequence[str], None] = 'd5e8a1c3b9f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("characters") as batch:
        batch.add_column(sa.Column("turnaround_look", sa.Text(), nullable=False,
                                   server_default=sa.text("''")))
        batch.drop_column("turnaround_plan")


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("characters") as batch:
        batch.add_column(sa.Column("turnaround_plan", sa.JSON(), nullable=False,
                                   server_default=sa.text("'{}'")))
        batch.drop_column("turnaround_look")
