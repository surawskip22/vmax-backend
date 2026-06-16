"""Normalize project status values for step 5A.

Revision ID: 20260615_06
Revises: 20260615_05
Create Date: 2026-06-15
"""

from alembic import op
import sqlalchemy as sa

from panmajster.db import Base


revision = "20260615_06"
down_revision = "20260615_05"
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


def qualified_table(table_name: str, schema: str | None) -> str:
    bind = op.get_bind()
    preparer = bind.dialect.identifier_preparer
    if not schema:
        return preparer.quote(table_name)
    return f"{preparer.quote_schema(schema)}.{preparer.quote(table_name)}"


def upgrade() -> None:
    schema = Base.metadata.schema
    if not has_column("projects", "status", schema):
        return
    bind = op.get_bind()
    projects_table = qualified_table("projects", schema)
    bind.execute(
        sa.text(
            f"UPDATE {projects_table} "
            "SET status = CASE "
            "WHEN status = 'completed' THEN 'completed' "
            "WHEN status = 'in_progress' THEN 'in_progress' "
            "ELSE 'assigned' "
            "END "
            "WHERE status IS NULL OR status NOT IN ('assigned', 'in_progress', 'completed')"
        )
    )


def downgrade() -> None:
    schema = Base.metadata.schema
    if not has_column("projects", "status", schema):
        return
    bind = op.get_bind()
    projects_table = qualified_table("projects", schema)
    bind.execute(
        sa.text(
            f"UPDATE {projects_table} "
            "SET status = CASE "
            "WHEN status = 'assigned' THEN 'active' "
            "WHEN status = 'in_progress' THEN 'active' "
            "ELSE status "
            "END"
        )
    )
