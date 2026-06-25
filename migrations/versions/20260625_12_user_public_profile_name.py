"""Add public profile name for contractor display.

Revision ID: 20260625_12
Revises: 20260625_11
Create Date: 2026-06-25
"""

from alembic import op
import sqlalchemy as sa

from panmajster.db import Base


revision = "20260625_12"
down_revision = "20260625_11"
branch_labels = None
depends_on = None


def has_column(table_name: str, column_name: str, schema: str | None) -> bool:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table_name, schema=schema):
        return False
    return any(
        column["name"] == column_name
        for column in inspector.get_columns(table_name, schema=schema)
    )


def upgrade() -> None:
    schema = Base.metadata.schema
    if not has_column("users", "public_profile_name", schema):
        op.add_column(
            "users",
            sa.Column(
                "public_profile_name",
                sa.String(120),
                nullable=False,
                server_default="",
            ),
            schema=schema,
        )


def downgrade() -> None:
    schema = Base.metadata.schema
    if has_column("users", "public_profile_name", schema):
        op.drop_column("users", "public_profile_name", schema=schema)
