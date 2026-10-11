"""project style preset

项目的风格预设（真人写实 / 3D 写实 / 日系动漫 / 国风漫画 / 美式漫画 / 商业广告）。已有项目都按真人写实。

Revision ID: a9c3e5f7b2d4
Revises: f2b6d8e1a4c7
Create Date: 2026-10-11 10:00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a9c3e5f7b2d4'
down_revision: Union[str, Sequence[str], None] = 'f2b6d8e1a4c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("projects") as batch:
        batch.add_column(sa.Column("style_preset", sa.String(length=32), nullable=False,
                                   server_default="live_action"))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("projects") as batch:
        batch.drop_column("style_preset")
