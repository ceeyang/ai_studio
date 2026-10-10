"""export final and project archived

成片定稿：exports.is_final（同集只允许一个 True，由应用层保证）。
项目归档：projects.archived（归档后默认列表不显示，?archived=1 才看得到）。

Revision ID: baedf06b3ae5
Revises: fce408674655
Create Date: 2026-10-10 18:33:28.492771

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'baedf06b3ae5'
down_revision: Union[str, Sequence[str], None] = 'fce408674655'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("exports") as batch:
        batch.add_column(sa.Column("is_final", sa.Boolean(), nullable=False,
                                   server_default=sa.text("0")))
    with op.batch_alter_table("projects") as batch:
        batch.add_column(sa.Column("archived", sa.Boolean(), nullable=False,
                                   server_default=sa.text("0")))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("projects") as batch:
        batch.drop_column("archived")
    with op.batch_alter_table("exports") as batch:
        batch.drop_column("is_final")
