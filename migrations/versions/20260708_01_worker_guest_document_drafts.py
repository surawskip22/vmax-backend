"""Track worker and guest document draft origins.

Revision ID: 20260708_01
Revises: 20260707_02
Create Date: 2026-07-08
"""

from alembic import op
import sqlalchemy as sa

from panmajster.db import Base


revision = "20260708_01"
down_revision = "20260707_02"
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


def add_origin_columns(table_name: str, schema: str | None) -> None:
    if not has_column(table_name, "draft_origin", schema):
        op.add_column(
            table_name,
            sa.Column("draft_origin", sa.String(40), nullable=False, server_default="manual"),
            schema=schema,
        )
    if not has_column(table_name, "draft_origin_label", schema):
        op.add_column(
            table_name,
            sa.Column("draft_origin_label", sa.String(180), nullable=False, server_default=""),
            schema=schema,
        )


def upgrade() -> None:
    schema = Base.metadata.schema
    add_origin_columns("estimates", schema)
    add_origin_columns("project_contracts", schema)


def downgrade() -> None:
    schema = Base.metadata.schema
    for table_name in ("project_contracts", "estimates"):
        if has_column(table_name, "draft_origin_label", schema):
            op.drop_column(table_name, "draft_origin_label", schema=schema)
        if has_column(table_name, "draft_origin", schema):
            op.drop_column(table_name, "draft_origin", schema=schema)
