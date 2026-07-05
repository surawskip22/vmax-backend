"""Add public share links for estimates.

Revision ID: 20260705_05
Revises: 20260705_04
Create Date: 2026-07-05
"""

from alembic import op
import sqlalchemy as sa

from panmajster.db import Base


revision = "20260705_05"
down_revision = "20260705_04"
branch_labels = None
depends_on = None


def has_table(table_name: str, schema: str | None) -> bool:
    return sa.inspect(op.get_bind()).has_table(table_name, schema=schema)


def has_column(table_name: str, column_name: str, schema: str | None) -> bool:
    if not has_table(table_name, schema):
        return False
    return any(
        column["name"] == column_name
        for column in sa.inspect(op.get_bind()).get_columns(table_name, schema=schema)
    )


def has_index(table_name: str, index_name: str, schema: str | None) -> bool:
    if not has_table(table_name, schema):
        return False
    return any(
        index["name"] == index_name
        for index in sa.inspect(op.get_bind()).get_indexes(table_name, schema=schema)
    )


def upgrade() -> None:
    schema = Base.metadata.schema
    if not has_table("estimates", schema):
        return

    if not has_column("estimates", "share_token", schema):
        op.add_column("estimates", sa.Column("share_token", sa.String(120), nullable=True), schema=schema)
    if not has_column("estimates", "share_active", schema):
        op.add_column(
            "estimates",
            sa.Column("share_active", sa.Boolean(), nullable=False, server_default=sa.false()),
            schema=schema,
        )
    if not has_column("estimates", "shared_at", schema):
        op.add_column("estimates", sa.Column("shared_at", sa.DateTime(timezone=True), nullable=True), schema=schema)
    if not has_index("estimates", "ix_estimates_share_token", schema):
        op.create_index("ix_estimates_share_token", "estimates", ["share_token"], unique=True, schema=schema)


def downgrade() -> None:
    schema = Base.metadata.schema
    if not has_table("estimates", schema):
        return

    if has_index("estimates", "ix_estimates_share_token", schema):
        op.drop_index("ix_estimates_share_token", table_name="estimates", schema=schema)
    if has_column("estimates", "shared_at", schema):
        op.drop_column("estimates", "shared_at", schema=schema)
    if has_column("estimates", "share_active", schema):
        op.drop_column("estimates", "share_active", schema=schema)
    if has_column("estimates", "share_token", schema):
        op.drop_column("estimates", "share_token", schema=schema)
