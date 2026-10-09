"""project end card

项目级片尾卡设置（三语店名、地址、营业时间、联系方式），合成导出时接在成片末尾。

Revision ID: a3f1c9d2e7b4
Revises: ed80f4d77578
Create Date: 2026-10-09 20:00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a3f1c9d2e7b4'
down_revision: Union[str, Sequence[str], None] = 'ed80f4d77578'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("projects") as batch:
        batch.add_column(sa.Column("end_card", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("projects") as batch:
        batch.drop_column("end_card")
