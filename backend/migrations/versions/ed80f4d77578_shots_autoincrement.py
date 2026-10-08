"""shots autoincrement

镜头 id 改成 AUTOINCREMENT：槽位按 (owner_type, owner_id) 挂在镜头上，id 复用会让新镜头继承旧候选。
autogenerate 检测不到 sqlite_autoincrement 的变化，这里手写重建表。

Revision ID: ed80f4d77578
Revises: c320d03cf109
Create Date: 2026-10-08 12:00:50.211291

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'ed80f4d77578'
down_revision: Union[str, Sequence[str], None] = 'c320d03cf109'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("shots", recreate="always",
                              table_kwargs={"sqlite_autoincrement": True}):
        pass


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("shots", recreate="always"):
        pass
