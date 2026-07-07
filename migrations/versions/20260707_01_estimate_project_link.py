"""Link accepted estimates to created projects.

Revision ID: 20260707_01
Revises: 20260705_05
Create Date: 2026-07-07
"""

from alembic import op
import sqlalchemy as sa

from panmajster.db import Base


revision = "20260707_01"
down_revision = "20260705_05"
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

    if not has_column("estimates", "project_id", schema):
        op.add_column("estimates", sa.Column("project_id", sa.String(36), nullable=True), schema=schema)
    if not has_index("estimates", "ix_estimates_project_id", schema):
        op.create_index("ix_estimates_project_id", "estimates", ["project_id"], unique=True, schema=schema)


def downgrade() -> None:
    schema = Base.metadata.schema
    if not has_table("estimates", schema):
        return

    if has_index("estimates", "ix_estimates_project_id", schema):
        op.drop_index("ix_estimates_project_id", table_name="estimates", schema=schema)
    if has_column("estimates", "project_id", schema):
        op.drop_column("estimates", "project_id", schema=schema)
