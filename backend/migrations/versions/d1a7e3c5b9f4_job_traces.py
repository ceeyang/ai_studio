"""job traces

任务的调用明细（job_traces）：每次 LLM 调用（提示词、原始回复、解析结果）、出图 / 视频引擎提交
（最终提示词、参考图、参数、workflow）、ffmpeg 命令各一条，任务详情页「调用明细」展示。
server_default 用普通字符串。

Revision ID: d1a7e3c5b9f4
Revises: c6d9e2f4a7b1
Create Date: 2026-10-11 22:00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'd1a7e3c5b9f4'
down_revision: Union[str, Sequence[str], None] = 'c6d9e2f4a7b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "job_traces",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("data", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default="2026-01-01 00:00:00"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "seq", name="uq_job_traces_job_seq"),
    )
    op.create_index("ix_job_traces_job_id", "job_traces", ["job_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_job_traces_job_id", table_name="job_traces")
    op.drop_table("job_traces")
