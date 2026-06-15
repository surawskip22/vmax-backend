"""Add profiles, simplified project controls and client link.

Revision ID: 20260612_02
Revises: 20260611_01
Create Date: 2026-06-12
"""

from datetime import datetime, timezone
from uuid import uuid4

from alembic import op
import sqlalchemy as sa

from panmajster.db import Base


revision = "20260612_02"
down_revision = "20260611_01"
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


def simplify_existing_project_stages(schema: str | None) -> None:
    bind = op.get_bind()
    metadata = sa.MetaData(schema=schema)
    projects = sa.Table("projects", metadata, autoload_with=bind)
    stages = sa.Table("project_stages", metadata, autoload_with=bind)
    entries = sa.Table("entries", metadata, autoload_with=bind)
    canonical_titles = (
        "Przed rozpoczęciem",
        "W trakcie realizacji",
        "Po zakończeniu",
    )

    for project_id, project_status in bind.execute(
        sa.select(projects.c.id, projects.c.status)
    ):
        existing = list(
            bind.execute(
                sa.select(
                    stages.c.id,
                    stages.c.title,
                    stages.c.position,
                    stages.c.status,
                )
                .where(stages.c.project_id == project_id)
                .order_by(stages.c.position)
            ).mappings()
        )
        if (
            len(existing) == 3
            and tuple(item["title"] for item in existing) == canonical_titles
        ):
            continue

        now = datetime.now(timezone.utc)
        has_completed = any(item["status"] == "completed" for item in existing)
        statuses = (
            ("completed", "completed", "completed")
            if project_status == "completed"
            else (
                ("completed", "active", "planned")
                if has_completed
                else ("active", "planned", "planned")
            )
        )
        new_ids = [str(uuid4()) for _ in canonical_titles]
        for index, (stage_id, title, status) in enumerate(
            zip(new_ids, canonical_titles, statuses, strict=True)
        ):
            bind.execute(
                stages.insert().values(
                    id=stage_id,
                    project_id=project_id,
                    title=title,
                    position=1000 + index,
                    status=status,
                    created_at=now,
                    updated_at=now,
                )
            )

        if existing:
            first_position = existing[0]["position"]
            last_position = existing[-1]["position"]
            for old_stage in existing:
                if len(existing) == 1:
                    target_index = 1
                elif old_stage["position"] == first_position:
                    target_index = 0
                elif old_stage["position"] == last_position:
                    target_index = 2
                else:
                    target_index = 1
                bind.execute(
                    entries.update()
                    .where(entries.c.stage_id == old_stage["id"])
                    .values(stage_id=new_ids[target_index])
                )
            bind.execute(
                stages.delete().where(
                    stages.c.id.in_([item["id"] for item in existing])
                )
            )

        for index, stage_id in enumerate(new_ids):
            bind.execute(
                stages.update()
                .where(stages.c.id == stage_id)
                .values(position=index)
            )


def upgrade() -> None:
    schema = Base.metadata.schema
    if not has_column("users", "profile_type", schema):
        op.add_column(
            "users",
            sa.Column("profile_type", sa.String(40), nullable=True),
            schema=schema,
        )
    if not has_column("users", "preferred_mode", schema):
        op.add_column(
            "users",
            sa.Column(
                "preferred_mode",
                sa.String(30),
                nullable=False,
                server_default="expanded",
            ),
            schema=schema,
        )
    if not has_column("workspaces", "description", schema):
        op.add_column(
            "workspaces",
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            schema=schema,
        )
    if not has_column("workspaces", "phone", schema):
        op.add_column(
            "workspaces",
            sa.Column("phone", sa.String(40), nullable=False, server_default=""),
            schema=schema,
        )
    if not has_column("workspaces", "address", schema):
        op.add_column(
            "workspaces",
            sa.Column("address", sa.String(300), nullable=False, server_default=""),
            schema=schema,
        )
    if not has_column("projects", "details_locked", schema):
        op.add_column(
            "projects",
            sa.Column(
                "details_locked",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
            schema=schema,
        )
    if not has_column("projects", "client_share_token", schema):
        op.add_column(
            "projects",
            sa.Column("client_share_token", sa.String(120), nullable=True),
            schema=schema,
        )
    if not has_column("projects", "client_share_active", schema):
        op.add_column(
            "projects",
            sa.Column(
                "client_share_active",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            ),
            schema=schema,
        )
    if not has_column("projects", "client_share_pin_hash", schema):
        op.add_column(
            "projects",
            sa.Column("client_share_pin_hash", sa.String(128), nullable=True),
            schema=schema,
        )
    if not has_index("projects", "ix_projects_client_share_token", schema):
        op.create_index(
            "ix_projects_client_share_token",
            "projects",
            ["client_share_token"],
            unique=True,
            schema=schema,
        )
    if not has_column("media_assets", "purpose", schema):
        op.add_column(
            "media_assets",
            sa.Column(
                "purpose",
                sa.String(40),
                nullable=False,
                server_default="attachment",
            ),
            schema=schema,
        )
    simplify_existing_project_stages(schema)


def downgrade() -> None:
    schema = Base.metadata.schema
    op.drop_column("media_assets", "purpose", schema=schema)
    op.drop_index(
        "ix_projects_client_share_token", table_name="projects", schema=schema
    )
    op.drop_column("projects", "client_share_pin_hash", schema=schema)
    op.drop_column("projects", "client_share_active", schema=schema)
    op.drop_column("projects", "client_share_token", schema=schema)
    op.drop_column("projects", "details_locked", schema=schema)
    op.drop_column("workspaces", "address", schema=schema)
    op.drop_column("workspaces", "phone", schema=schema)
    op.drop_column("workspaces", "description", schema=schema)
    op.drop_column("users", "preferred_mode", schema=schema)
    op.drop_column("users", "profile_type", schema=schema)
