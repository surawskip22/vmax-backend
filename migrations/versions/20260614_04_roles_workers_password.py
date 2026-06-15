"""Add password login and worker profiles.

Revision ID: 20260614_04
Revises: 20260614_03
Create Date: 2026-06-14
"""

from alembic import op
import sqlalchemy as sa

from panmajster.db import Base


revision = "20260614_04"
down_revision = "20260614_03"
branch_labels = None
depends_on = None


def has_column(table_name: str, column_name: str, schema: str | None) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(
        column["name"] == column_name
        for column in inspector.get_columns(table_name, schema=schema)
    )


def has_table(table_name: str, schema: str | None) -> bool:
    inspector = sa.inspect(op.get_bind())
    return table_name in inspector.get_table_names(schema=schema)


def has_index(table_name: str, index_name: str, schema: str | None) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(
        index["name"] == index_name
        for index in inspector.get_indexes(table_name, schema=schema)
    )


def upgrade() -> None:
    schema = Base.metadata.schema
    if not has_column("users", "password_hash", schema):
        op.add_column(
            "users",
            sa.Column("password_hash", sa.String(128), nullable=False, server_default=""),
            schema=schema,
        )
    if not has_table("worker_profiles", schema):
        op.create_table(
            "worker_profiles",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("owner_id", sa.String(36), nullable=False),
            sa.Column("workspace_id", sa.String(36), nullable=True),
            sa.Column("label", sa.String(160), nullable=False),
            sa.Column("email", sa.String(320), nullable=False, server_default=""),
            sa.Column("phone", sa.String(40), nullable=False, server_default=""),
            sa.Column("note", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
            ),
            schema=schema,
        )
    if not has_index("worker_profiles", "ix_worker_profiles_workspace_id", schema):
        op.create_index(
            "ix_worker_profiles_workspace_id",
            "worker_profiles",
            ["workspace_id"],
            schema=schema,
        )
    if not has_column("projects", "worker_profile_id", schema):
        op.add_column(
            "projects",
            sa.Column("worker_profile_id", sa.String(36), nullable=True),
            schema=schema,
        )
    if not has_index("projects", "ix_projects_worker_profile_id", schema):
        op.create_index(
            "ix_projects_worker_profile_id",
            "projects",
            ["worker_profile_id"],
            schema=schema,
        )
    if not has_column("guest_invites", "worker_profile_id", schema):
        op.add_column(
            "guest_invites",
            sa.Column("worker_profile_id", sa.String(36), nullable=True),
            schema=schema,
        )
    if not has_index("guest_invites", "ix_guest_invites_worker_profile_id", schema):
        op.create_index(
            "ix_guest_invites_worker_profile_id",
            "guest_invites",
            ["worker_profile_id"],
            schema=schema,
        )


def downgrade() -> None:
    schema = Base.metadata.schema
    if has_index("guest_invites", "ix_guest_invites_worker_profile_id", schema):
        op.drop_index(
            "ix_guest_invites_worker_profile_id",
            table_name="guest_invites",
            schema=schema,
        )
    if has_column("guest_invites", "worker_profile_id", schema):
        op.drop_column("guest_invites", "worker_profile_id", schema=schema)
    if has_index("projects", "ix_projects_worker_profile_id", schema):
        op.drop_index(
            "ix_projects_worker_profile_id", table_name="projects", schema=schema
        )
    if has_column("projects", "worker_profile_id", schema):
        op.drop_column("projects", "worker_profile_id", schema=schema)
    if has_table("worker_profiles", schema):
        op.drop_table("worker_profiles", schema=schema)
    if has_column("users", "password_hash", schema):
        op.drop_column("users", "password_hash", schema=schema)
