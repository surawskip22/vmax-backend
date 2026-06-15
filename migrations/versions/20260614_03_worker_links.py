"""Extend guest invites for worker links.

Revision ID: 20260614_03
Revises: 20260612_02
Create Date: 2026-06-14
"""

from alembic import op
import sqlalchemy as sa

from panmajster.db import Base


revision = "20260614_03"
down_revision = "20260612_02"
branch_labels = None
depends_on = None


def has_column(table_name: str, column_name: str, schema: str | None) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(
        column["name"] == column_name
        for column in inspector.get_columns(table_name, schema=schema)
    )


def has_index(table_name: str, index_name: str, schema: str | None) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(
        index["name"] == index_name
        for index in inspector.get_indexes(table_name, schema=schema)
    )


def upgrade() -> None:
    schema = Base.metadata.schema
    if not has_column("guest_invites", "workspace_id", schema):
        op.add_column(
            "guest_invites",
            sa.Column("workspace_id", sa.String(36), nullable=True),
            schema=schema,
        )
    if not has_index("guest_invites", "ix_guest_invites_workspace_id", schema):
        op.create_index(
            "ix_guest_invites_workspace_id",
            "guest_invites",
            ["workspace_id"],
            schema=schema,
        )
    if not has_column("guest_invites", "email", schema):
        op.add_column(
            "guest_invites",
            sa.Column("email", sa.String(320), nullable=False, server_default=""),
            schema=schema,
        )
    if not has_column("guest_invites", "kind", schema):
        op.add_column(
            "guest_invites",
            sa.Column(
                "kind",
                sa.String(30),
                nullable=False,
                server_default="guest",
            ),
            schema=schema,
        )


def downgrade() -> None:
    schema = Base.metadata.schema
    if has_column("guest_invites", "kind", schema):
        op.drop_column("guest_invites", "kind", schema=schema)
    if has_column("guest_invites", "email", schema):
        op.drop_column("guest_invites", "email", schema=schema)
    if has_index("guest_invites", "ix_guest_invites_workspace_id", schema):
        op.drop_index(
            "ix_guest_invites_workspace_id",
            table_name="guest_invites",
            schema=schema,
        )
    if has_column("guest_invites", "workspace_id", schema):
        op.drop_column("guest_invites", "workspace_id", schema=schema)
