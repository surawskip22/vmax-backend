"""Repair user columns for schema deployments.

Revision ID: 20260616_08
Revises: 20260615_07
Create Date: 2026-06-16
"""

from alembic import op
import sqlalchemy as sa

from panmajster.db import Base


revision = "20260616_08"
down_revision = "20260615_07"
branch_labels = None
depends_on = None


def existing_columns(table_name: str, schema: str | None) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table_name, schema=schema):
        return set()
    return {
        column["name"]
        for column in inspector.get_columns(table_name, schema=schema)
    }


def add_column_if_missing(
    table_name: str,
    column_name: str,
    column: sa.Column,
    schema: str | None,
    columns: set[str],
) -> None:
    if column_name in columns:
        return
    op.add_column(table_name, column, schema=schema)
    columns.add(column_name)


def upgrade() -> None:
    schema = Base.metadata.schema
    columns = existing_columns("users", schema)
    if not columns:
        return

    add_column_if_missing(
        "users",
        "name",
        sa.Column("name", sa.String(160), nullable=True, server_default=""),
        schema,
        columns,
    )
    add_column_if_missing(
        "users",
        "phone",
        sa.Column("phone", sa.String(40), nullable=True),
        schema,
        columns,
    )
    add_column_if_missing(
        "users",
        "is_admin",
        sa.Column("is_admin", sa.Boolean(), nullable=True, server_default=sa.false()),
        schema,
        columns,
    )
    add_column_if_missing(
        "users",
        "locale",
        sa.Column("locale", sa.String(10), nullable=True, server_default="pl"),
        schema,
        columns,
    )
    add_column_if_missing(
        "users",
        "profile_type",
        sa.Column("profile_type", sa.String(40), nullable=True),
        schema,
        columns,
    )
    add_column_if_missing(
        "users",
        "preferred_mode",
        sa.Column(
            "preferred_mode",
            sa.String(30),
            nullable=True,
            server_default="expanded",
        ),
        schema,
        columns,
    )
    add_column_if_missing(
        "users",
        "password_hash",
        sa.Column("password_hash", sa.String(128), nullable=True, server_default=""),
        schema,
        columns,
    )
    add_column_if_missing(
        "users",
        "last_login_at",
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        schema,
        columns,
    )
    add_column_if_missing(
        "users",
        "created_at",
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        schema,
        columns,
    )
    add_column_if_missing(
        "users",
        "updated_at",
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        schema,
        columns,
    )


def downgrade() -> None:
    pass
