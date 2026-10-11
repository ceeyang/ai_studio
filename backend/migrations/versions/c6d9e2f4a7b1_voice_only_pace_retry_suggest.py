"""voice-only characters, project shot pace and auto retry, storyboard continue suggestion

- characters.voice_only：只出声、不出镜的角色（画外音、电话里的声音）。剧本结构 description 写了
  「只出声」时自动打上；不抽定妆照、分镜不给外貌占位符。
- projects.shot_seconds：项目的镜头节奏（每镜几秒，3 / 4 / 5）；空 = 跟随视频引擎的甜区。
- projects.auto_retry_video：视频开头没接上首帧时自动换种子重拍一次。
- shots.suggest_continue：分镜建议这一镜「接上一镜」（只是建议，用户一键打开）。

全部用 op.add_column（SQLite 的 ALTER TABLE ADD COLUMN），server_default 用普通字符串：
batch 重建 shots 表会丢掉 AUTOINCREMENT（见 c8a4f1e2d6b9）。

Revision ID: c6d9e2f4a7b1
Revises: b4e8d2c6f1a3
Create Date: 2026-10-11 20:00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'c6d9e2f4a7b1'
down_revision: Union[str, Sequence[str], None] = 'b4e8d2c6f1a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("characters", sa.Column("voice_only", sa.Boolean(), nullable=False,
                                          server_default="0"))
    op.add_column("projects", sa.Column("shot_seconds", sa.Float(), nullable=True))
    op.add_column("projects", sa.Column("auto_retry_video", sa.Boolean(), nullable=False,
                                        server_default="0"))
    op.add_column("shots", sa.Column("suggest_continue", sa.Boolean(), nullable=False,
                                     server_default="0"))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("characters") as batch:
        batch.drop_column("voice_only")
    with op.batch_alter_table("projects") as batch:
        batch.drop_column("auto_retry_video")
        batch.drop_column("shot_seconds")
    # shots 不走 batch 重建（会丢 AUTOINCREMENT）：SQLite 3.35+ 支持 DROP COLUMN
    op.drop_column("shots", "suggest_continue")
