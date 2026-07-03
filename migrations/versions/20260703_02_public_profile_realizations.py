"""Add public profile realizations.

Revision ID: 20260703_02
Revises: 20260703_01
Create Date: 2026-07-03
"""

from alembic import op
import sqlalchemy as sa

from panmajster.db import Base


revision = "20260703_02"
down_revision = "20260703_01"
branch_labels = None
depends_on = None


def has_table(table_name: str, schema: str | None) -> bool:
    return sa.inspect(op.get_bind()).has_table(table_name, schema=schema)


def upgrade() -> None:
    schema = Base.metadata.schema
    if has_table("public_profile_realizations", schema):
        return

    op.create_table(
        "public_profile_realizations",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("owner_type", sa.String(40), nullable=False),
        sa.Column("owner_id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=True),
        sa.Column("title", sa.String(220), nullable=False, server_default=""),
        sa.Column("public_description", sa.Text(), nullable=False, server_default=""),
        sa.Column("location_public", sa.String(220), nullable=False, server_default=""),
        sa.Column("work_scope", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("completion_date", sa.Date(), nullable=True),
        sa.Column("amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False, server_default="PLN"),
        sa.Column("show_amount", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(30), nullable=False, server_default="draft"),
        sa.Column("cover_image_url", sa.Text(), nullable=False, server_default=""),
        sa.Column("gallery_image_urls", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        schema=schema,
    )
    op.create_index(
        "ix_public_profile_realizations_owner_type",
        "public_profile_realizations",
        ["owner_type"],
        schema=schema,
    )
    op.create_index(
        "ix_public_profile_realizations_owner_id",
        "public_profile_realizations",
        ["owner_id"],
        schema=schema,
    )
    op.create_index(
        "ix_public_profile_realizations_project_id",
        "public_profile_realizations",
        ["project_id"],
        schema=schema,
    )
    op.create_index(
        "ix_public_profile_realizations_status",
        "public_profile_realizations",
        ["status"],
        schema=schema,
    )
    op.create_index(
        "ix_public_profile_realizations_owner",
        "public_profile_realizations",
        ["owner_type", "owner_id"],
        schema=schema,
    )
    op.create_index(
        "ix_public_profile_realizations_public",
        "public_profile_realizations",
        ["owner_type", "owner_id", "status"],
        schema=schema,
    )


def downgrade() -> None:
    schema = Base.metadata.schema
    if not has_table("public_profile_realizations", schema):
        return

    op.drop_index("ix_public_profile_realizations_public", table_name="public_profile_realizations", schema=schema)
    op.drop_index("ix_public_profile_realizations_owner", table_name="public_profile_realizations", schema=schema)
    op.drop_index("ix_public_profile_realizations_status", table_name="public_profile_realizations", schema=schema)
    op.drop_index("ix_public_profile_realizations_project_id", table_name="public_profile_realizations", schema=schema)
    op.drop_index("ix_public_profile_realizations_owner_id", table_name="public_profile_realizations", schema=schema)
    op.drop_index("ix_public_profile_realizations_owner_type", table_name="public_profile_realizations", schema=schema)
    op.drop_table("public_profile_realizations", schema=schema)
