"""Add public profiles for contractors and companies.

Revision ID: 20260703_01
Revises: 20260625_12
Create Date: 2026-07-03
"""

from alembic import op
import sqlalchemy as sa

from panmajster.db import Base


revision = "20260703_01"
down_revision = "20260625_12"
branch_labels = None
depends_on = None


def has_table(table_name: str, schema: str | None) -> bool:
    return sa.inspect(op.get_bind()).has_table(table_name, schema=schema)


def upgrade() -> None:
    schema = Base.metadata.schema
    if has_table("public_profiles", schema):
        return

    op.create_table(
        "public_profiles",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("owner_type", sa.String(40), nullable=False),
        sa.Column("owner_id", sa.String(36), nullable=False),
        sa.Column("display_name", sa.String(180), nullable=False, server_default=""),
        sa.Column("public_description", sa.Text(), nullable=False, server_default=""),
        sa.Column("contact_phone", sa.String(40), nullable=False, server_default=""),
        sa.Column("contact_email", sa.String(320), nullable=False, server_default=""),
        sa.Column("specializations", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("service_area", sa.String(220), nullable=False, server_default=""),
        sa.Column("is_public", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("slug", sa.String(140), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_type", "owner_id", name="uq_public_profiles_owner"),
        sa.UniqueConstraint("slug", name="uq_public_profiles_slug"),
        schema=schema,
    )
    op.create_index(
        "ix_public_profiles_owner_type",
        "public_profiles",
        ["owner_type"],
        schema=schema,
    )
    op.create_index(
        "ix_public_profiles_owner_id",
        "public_profiles",
        ["owner_id"],
        schema=schema,
    )
    op.create_index(
        "ix_public_profiles_slug",
        "public_profiles",
        ["slug"],
        schema=schema,
    )


def downgrade() -> None:
    schema = Base.metadata.schema
    if not has_table("public_profiles", schema):
        return

    op.drop_index("ix_public_profiles_slug", table_name="public_profiles", schema=schema)
    op.drop_index("ix_public_profiles_owner_id", table_name="public_profiles", schema=schema)
    op.drop_index("ix_public_profiles_owner_type", table_name="public_profiles", schema=schema)
    op.drop_table("public_profiles", schema=schema)
