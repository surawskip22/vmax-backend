"""Add comment intent and author type fields.

Revision ID: 20260625_11
Revises: 20260625_10
Create Date: 2026-06-25
"""

from alembic import op
import sqlalchemy as sa

from panmajster.db import Base


revision = "20260625_11"
down_revision = "20260625_10"
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
    if not has_column("comments", "author_type", schema):
        op.add_column(
            "comments",
            sa.Column("author_type", sa.String(30), nullable=False, server_default="user"),
            schema=schema,
        )
    if not has_column("comments", "author_label", schema):
        op.add_column(
            "comments",
            sa.Column("author_label", sa.String(160), nullable=True),
            schema=schema,
        )
    if not has_column("comments", "intent", schema):
        op.add_column(
            "comments",
            sa.Column("intent", sa.String(40), nullable=False, server_default="comment"),
            schema=schema,
        )


def downgrade() -> None:
    schema = Base.metadata.schema
    if has_column("comments", "intent", schema):
        op.drop_column("comments", "intent", schema=schema)
    if has_column("comments", "author_label", schema):
        op.drop_column("comments", "author_label", schema=schema)
    if has_column("comments", "author_type", schema):
        op.drop_column("comments", "author_type", schema=schema)
