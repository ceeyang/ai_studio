"""shot origin

镜头来源：llm（分镜生成，重跑会被替换/清理）/ manual（用户手工加的，重跑分镜要保留）。

Revision ID: fce408674655
Revises: e7c4b2a9d1f3
Create Date: 2026-10-10 10:00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'fce408674655'
down_revision: Union[str, Sequence[str], None] = 'e7c4b2a9d1f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("shots") as batch:
        batch.add_column(sa.Column("origin", sa.String(length=16), nullable=False,
                                   server_default=sa.text("'llm'")))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("shots") as batch:
        batch.drop_column("origin")
