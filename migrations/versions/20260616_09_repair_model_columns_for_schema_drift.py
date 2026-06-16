"""Repair model columns for schema drift.

Revision ID: 20260616_09
Revises: 20260616_08
Create Date: 2026-06-16
"""

from alembic import op
import sqlalchemy as sa

from panmajster import models  # noqa: F401
from panmajster.db import Base


revision = "20260616_09"
down_revision = "20260616_08"
branch_labels = None
depends_on = None


SAFE_COLUMN_DEFAULTS = {
    ("users", "name"): "''",
    ("users", "is_admin"): "false",
    ("users", "locale"): "'pl'",
    ("users", "preferred_mode"): "'expanded'",
    ("users", "password_hash"): "''",
    ("workspaces", "description"): "''",
    ("workspaces", "phone"): "''",
    ("workspaces", "address"): "''",
    ("projects", "status"): "'assigned'",
    ("projects", "contract_currency"): "'PLN'",
}


def existing_columns(table_name: str, schema: str | None) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table_name, schema=schema):
        return set()
    return {
        column["name"]
        for column in inspector.get_columns(table_name, schema=schema)
    }


def additive_column(table_name: str, model_column) -> sa.Column:
    kwargs = {"nullable": True}
    default = SAFE_COLUMN_DEFAULTS.get((table_name, model_column.name))
    if default is not None:
        kwargs["server_default"] = sa.text(default)
    return sa.Column(model_column.name, model_column.type, **kwargs)


def upgrade() -> None:
    schema = Base.metadata.schema
    for table in Base.metadata.sorted_tables:
        columns = existing_columns(table.name, schema)
        if not columns:
            continue
        for model_column in table.columns:
            if model_column.name in columns or model_column.primary_key:
                continue
            op.add_column(
                table.name,
                additive_column(table.name, model_column),
                schema=schema,
            )
            columns.add(model_column.name)


def downgrade() -> None:
    pass
