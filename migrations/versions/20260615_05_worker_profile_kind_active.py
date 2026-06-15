"""Add worker profile type and active flag.

Revision ID: 20260615_05
Revises: 20260614_04
Create Date: 2026-06-15
"""

from alembic import op
import sqlalchemy as sa

from panmajster.db import Base


revision = "20260615_05"
down_revision = "20260614_04"
branch_labels = None
depends_on = None


def has_column(table_name: str, column_name: str, schema: str | None) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(
        column["name"] == column_name
        for column in inspector.get_columns(table_name, schema=schema)
    )


def upgrade() -> None:
    schema = Base.metadata.schema
    if not has_column("worker_profiles", "profile_kind", schema):
        op.add_column(
            "worker_profiles",
            sa.Column(
                "profile_kind",
                sa.String(30),
                nullable=False,
                server_default="craftsman",
            ),
            schema=schema,
        )
    if not has_column("worker_profiles", "active", schema):
        op.add_column(
            "worker_profiles",
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
            schema=schema,
        )


def downgrade() -> None:
    schema = Base.metadata.schema
    if has_column("worker_profiles", "active", schema):
        op.drop_column("worker_profiles", "active", schema=schema)
    if has_column("worker_profiles", "profile_kind", schema):
        op.drop_column("worker_profiles", "profile_kind", schema=schema)
