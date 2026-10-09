"""character design options

角色的 AI 推荐造型（character.suggest 的结果）：[{label, visual_prompt, core_features}]。

Revision ID: b7d2e4f6a8c1
Revises: a3f1c9d2e7b4
Create Date: 2026-10-09 22:00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'b7d2e4f6a8c1'
down_revision: Union[str, Sequence[str], None] = 'a3f1c9d2e7b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("characters") as batch:
        batch.add_column(sa.Column("design_options", sa.JSON(), nullable=False,
                                   server_default=sa.text("'[]'")))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("characters") as batch:
        batch.drop_column("design_options")
