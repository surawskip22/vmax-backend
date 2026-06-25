"""Add client cover media selection for public project links.

Revision ID: 20260625_10
Revises: 20260616_09
Create Date: 2026-06-25
"""

from alembic import op
import sqlalchemy as sa

from panmajster.db import Base


revision = "20260625_10"
down_revision = "20260616_09"
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
    if not has_column("projects", "client_cover_media_id", schema):
        op.add_column(
            "projects",
            sa.Column("client_cover_media_id", sa.String(36), nullable=True),
            schema=schema,
        )


def downgrade() -> None:
    schema = Base.metadata.schema
    if has_column("projects", "client_cover_media_id", schema):
        op.drop_column("projects", "client_cover_media_id", schema=schema)
