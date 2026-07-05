"""Add job posting interests.

Revision ID: 20260705_02
Revises: 20260705_01
Create Date: 2026-07-05
"""

from alembic import op
import sqlalchemy as sa

from panmajster.db import Base


revision = "20260705_02"
down_revision = "20260705_01"
branch_labels = None
depends_on = None


def has_table(table_name: str, schema: str | None) -> bool:
    return sa.inspect(op.get_bind()).has_table(table_name, schema=schema)


def upgrade() -> None:
    schema = Base.metadata.schema
    if has_table("job_posting_interests", schema):
        return

    op.create_table(
        "job_posting_interests",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("job_posting_id", sa.String(36), nullable=False),
        sa.Column("contractor_owner_type", sa.String(40), nullable=False),
        sa.Column("contractor_owner_id", sa.String(36), nullable=False),
        sa.Column("public_profile_id", sa.String(36), nullable=False),
        sa.Column("message", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(30), nullable=False, server_default="new"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_posting_id"], ["job_postings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["public_profile_id"], ["public_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "job_posting_id",
            "contractor_owner_type",
            "contractor_owner_id",
            name="uq_job_posting_interest_contractor",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_job_posting_interests_public_profile_id",
        "job_posting_interests",
        ["public_profile_id"],
        schema=schema,
    )
    op.create_index(
        "ix_job_posting_interests_status",
        "job_posting_interests",
        ["status"],
        schema=schema,
    )
    op.create_index(
        "ix_job_posting_interests_posting",
        "job_posting_interests",
        ["job_posting_id"],
        schema=schema,
    )
    op.create_index(
        "ix_job_posting_interests_contractor",
        "job_posting_interests",
        ["contractor_owner_type", "contractor_owner_id"],
        schema=schema,
    )


def downgrade() -> None:
    schema = Base.metadata.schema
    if not has_table("job_posting_interests", schema):
        return

    op.drop_index("ix_job_posting_interests_contractor", table_name="job_posting_interests", schema=schema)
    op.drop_index("ix_job_posting_interests_posting", table_name="job_posting_interests", schema=schema)
    op.drop_index("ix_job_posting_interests_status", table_name="job_posting_interests", schema=schema)
    op.drop_index("ix_job_posting_interests_public_profile_id", table_name="job_posting_interests", schema=schema)
    op.drop_table("job_posting_interests", schema=schema)
