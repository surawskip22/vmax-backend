"""Add investor job postings.

Revision ID: 20260705_01
Revises: 20260703_02
Create Date: 2026-07-05
"""

from alembic import op
import sqlalchemy as sa

from panmajster.db import Base


revision = "20260705_01"
down_revision = "20260703_02"
branch_labels = None
depends_on = None


def has_table(table_name: str, schema: str | None) -> bool:
    return sa.inspect(op.get_bind()).has_table(table_name, schema=schema)


def upgrade() -> None:
    schema = Base.metadata.schema
    if has_table("job_postings", schema):
        return

    op.create_table(
        "job_postings",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("investor_id", sa.String(36), nullable=False),
        sa.Column("title", sa.String(220), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("location", sa.String(220), nullable=False, server_default=""),
        sa.Column("budget_label", sa.String(120), nullable=False, server_default=""),
        sa.Column("deadline", sa.String(160), nullable=False, server_default=""),
        sa.Column("specializations", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("current_state_description", sa.Text(), nullable=False, server_default=""),
        sa.Column("target_contractor_type", sa.String(40), nullable=False, server_default="any"),
        sa.Column("status", sa.String(30), nullable=False, server_default="draft"),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["investor_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        schema=schema,
    )
    op.create_index(
        "ix_job_postings_investor_id",
        "job_postings",
        ["investor_id"],
        schema=schema,
    )
    op.create_index(
        "ix_job_postings_status",
        "job_postings",
        ["status"],
        schema=schema,
    )
    op.create_index(
        "ix_job_postings_public",
        "job_postings",
        ["status", "published_at"],
        schema=schema,
    )


def downgrade() -> None:
    schema = Base.metadata.schema
    if not has_table("job_postings", schema):
        return

    op.drop_index("ix_job_postings_public", table_name="job_postings", schema=schema)
    op.drop_index("ix_job_postings_status", table_name="job_postings", schema=schema)
    op.drop_index("ix_job_postings_investor_id", table_name="job_postings", schema=schema)
    op.drop_table("job_postings", schema=schema)
