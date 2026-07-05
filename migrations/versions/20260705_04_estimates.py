"""Add general estimates.

Revision ID: 20260705_04
Revises: 20260705_03
Create Date: 2026-07-05
"""

from alembic import op
import sqlalchemy as sa

from panmajster.db import Base


revision = "20260705_04"
down_revision = "20260705_03"
branch_labels = None
depends_on = None


def has_table(table_name: str, schema: str | None) -> bool:
    return sa.inspect(op.get_bind()).has_table(table_name, schema=schema)


def upgrade() -> None:
    schema = Base.metadata.schema
    if has_table("estimates", schema):
        return

    op.create_table(
        "estimates",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("owner_type", sa.String(40), nullable=False),
        sa.Column("owner_id", sa.String(36), nullable=False),
        sa.Column("created_by_id", sa.String(36), nullable=False),
        sa.Column("approved_by_id", sa.String(36), nullable=True),
        sa.Column("recipient_type", sa.String(30), nullable=False, server_default="manual"),
        sa.Column("recipient_name", sa.String(180), nullable=False, server_default=""),
        sa.Column("recipient_email", sa.String(320), nullable=False, server_default=""),
        sa.Column("recipient_phone", sa.String(40), nullable=False, server_default=""),
        sa.Column("source_type", sa.String(40), nullable=False, server_default="manual"),
        sa.Column("source_id", sa.String(36), nullable=True),
        sa.Column("title", sa.String(220), nullable=False, server_default=""),
        sa.Column("scope_summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("assumptions", sa.Text(), nullable=False, server_default=""),
        sa.Column("estimated_price", sa.Numeric(12, 2), nullable=True),
        sa.Column("price_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("planned_start", sa.String(160), nullable=False, server_default=""),
        sa.Column("planned_end", sa.String(160), nullable=False, server_default=""),
        sa.Column("status", sa.String(30), nullable=False, server_default="draft"),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["approved_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        schema=schema,
    )
    op.create_index("ix_estimates_owner_type", "estimates", ["owner_type"], schema=schema)
    op.create_index("ix_estimates_owner_id", "estimates", ["owner_id"], schema=schema)
    op.create_index("ix_estimates_created_by_id", "estimates", ["created_by_id"], schema=schema)
    op.create_index("ix_estimates_source_id", "estimates", ["source_id"], schema=schema)
    op.create_index("ix_estimates_status", "estimates", ["status"], schema=schema)
    op.create_index("ix_estimates_owner", "estimates", ["owner_type", "owner_id"], schema=schema)
    op.create_index("ix_estimates_source", "estimates", ["source_type", "source_id"], schema=schema)


def downgrade() -> None:
    schema = Base.metadata.schema
    if not has_table("estimates", schema):
        return

    op.drop_index("ix_estimates_source", table_name="estimates", schema=schema)
    op.drop_index("ix_estimates_owner", table_name="estimates", schema=schema)
    op.drop_index("ix_estimates_status", table_name="estimates", schema=schema)
    op.drop_index("ix_estimates_source_id", table_name="estimates", schema=schema)
    op.drop_index("ix_estimates_created_by_id", table_name="estimates", schema=schema)
    op.drop_index("ix_estimates_owner_id", table_name="estimates", schema=schema)
    op.drop_index("ix_estimates_owner_type", table_name="estimates", schema=schema)
    op.drop_table("estimates", schema=schema)
