"""Add job posting offers.

Revision ID: 20260705_03
Revises: 20260705_02
Create Date: 2026-07-05
"""

from alembic import op
import sqlalchemy as sa

from panmajster.db import Base


revision = "20260705_03"
down_revision = "20260705_02"
branch_labels = None
depends_on = None


def has_table(table_name: str, schema: str | None) -> bool:
    return sa.inspect(op.get_bind()).has_table(table_name, schema=schema)


def upgrade() -> None:
    schema = Base.metadata.schema
    if has_table("job_posting_offers", schema):
        return

    op.create_table(
        "job_posting_offers",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("job_posting_id", sa.String(36), nullable=False),
        sa.Column("interest_id", sa.String(36), nullable=False),
        sa.Column("contractor_owner_type", sa.String(40), nullable=False),
        sa.Column("contractor_owner_id", sa.String(36), nullable=False),
        sa.Column("public_profile_id", sa.String(36), nullable=False),
        sa.Column("title", sa.String(220), nullable=False, server_default=""),
        sa.Column("scope_summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("assumptions", sa.Text(), nullable=False, server_default=""),
        sa.Column("estimated_price", sa.Numeric(12, 2), nullable=True),
        sa.Column("price_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("planned_start", sa.String(160), nullable=False, server_default=""),
        sa.Column("planned_end", sa.String(160), nullable=False, server_default=""),
        sa.Column("status", sa.String(30), nullable=False, server_default="draft"),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_posting_id"], ["job_postings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["interest_id"], ["job_posting_interests.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["public_profile_id"], ["public_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "job_posting_id",
            "contractor_owner_type",
            "contractor_owner_id",
            name="uq_job_posting_offer_contractor",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_job_posting_offers_interest_id",
        "job_posting_offers",
        ["interest_id"],
        schema=schema,
    )
    op.create_index(
        "ix_job_posting_offers_public_profile_id",
        "job_posting_offers",
        ["public_profile_id"],
        schema=schema,
    )
    op.create_index(
        "ix_job_posting_offers_status",
        "job_posting_offers",
        ["status"],
        schema=schema,
    )
    op.create_index(
        "ix_job_posting_offers_posting",
        "job_posting_offers",
        ["job_posting_id"],
        schema=schema,
    )
    op.create_index(
        "ix_job_posting_offers_contractor",
        "job_posting_offers",
        ["contractor_owner_type", "contractor_owner_id"],
        schema=schema,
    )


def downgrade() -> None:
    schema = Base.metadata.schema
    if not has_table("job_posting_offers", schema):
        return

    op.drop_index("ix_job_posting_offers_contractor", table_name="job_posting_offers", schema=schema)
    op.drop_index("ix_job_posting_offers_posting", table_name="job_posting_offers", schema=schema)
    op.drop_index("ix_job_posting_offers_status", table_name="job_posting_offers", schema=schema)
    op.drop_index("ix_job_posting_offers_public_profile_id", table_name="job_posting_offers", schema=schema)
    op.drop_index("ix_job_posting_offers_interest_id", table_name="job_posting_offers", schema=schema)
    op.drop_table("job_posting_offers", schema=schema)
