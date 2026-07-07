"""Add project contracts.

Revision ID: 20260707_02
Revises: 20260707_01
Create Date: 2026-07-07
"""

from alembic import op
import sqlalchemy as sa

from panmajster.db import Base


revision = "20260707_02"
down_revision = "20260707_01"
branch_labels = None
depends_on = None


def has_table(table_name: str, schema: str | None) -> bool:
    return sa.inspect(op.get_bind()).has_table(table_name, schema=schema)


def has_index(table_name: str, index_name: str, schema: str | None) -> bool:
    if not has_table(table_name, schema):
        return False
    return any(
        index["name"] == index_name
        for index in sa.inspect(op.get_bind()).get_indexes(table_name, schema=schema)
    )


def has_constraint(table_name: str, constraint_name: str, schema: str | None) -> bool:
    if not has_table(table_name, schema):
        return False
    inspector = sa.inspect(op.get_bind())
    return any(
        constraint["name"] == constraint_name
        for constraint in inspector.get_unique_constraints(table_name, schema=schema)
    )


def upgrade() -> None:
    schema = Base.metadata.schema
    if not has_table("project_contracts", schema):
        op.create_table(
            "project_contracts",
            sa.Column("id", sa.String(36), nullable=False),
            sa.Column("project_id", sa.String(36), nullable=False),
            sa.Column("owner_type", sa.String(40), nullable=False),
            sa.Column("owner_id", sa.String(36), nullable=False),
            sa.Column("company_id", sa.String(36), nullable=True),
            sa.Column("created_by_id", sa.String(36), nullable=False),
            sa.Column("status", sa.String(30), nullable=False),
            sa.Column("share_token", sa.String(120), nullable=True),
            sa.Column("share_active", sa.Boolean(), nullable=False),
            sa.Column("contract_number", sa.String(60), nullable=False),
            sa.Column("contractor_name", sa.String(180), nullable=False),
            sa.Column("contractor_email", sa.String(320), nullable=False),
            sa.Column("contractor_phone", sa.String(40), nullable=False),
            sa.Column("client_name", sa.String(180), nullable=False),
            sa.Column("client_email", sa.String(320), nullable=False),
            sa.Column("client_phone", sa.String(40), nullable=False),
            sa.Column("work_address", sa.String(300), nullable=False),
            sa.Column("project_name", sa.String(220), nullable=False),
            sa.Column("scope_summary", sa.Text(), nullable=False),
            sa.Column("terms_summary", sa.Text(), nullable=False),
            sa.Column("planned_start", sa.String(160), nullable=False),
            sa.Column("planned_end", sa.String(160), nullable=False),
            sa.Column("price_amount", sa.Numeric(12, 2), nullable=True),
            sa.Column("price_currency", sa.String(3), nullable=False),
            sa.Column("price_note", sa.Text(), nullable=False),
            sa.Column("deposit_amount", sa.Numeric(12, 2), nullable=True),
            sa.Column("change_terms", sa.Text(), nullable=False),
            sa.Column("attachments_note", sa.Text(), nullable=False),
            sa.Column("legal_note", sa.Text(), nullable=False),
            sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
            sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("project_id", name="uq_project_contracts_project_id"),
            schema=schema,
        )

    indexes = [
        ("ix_project_contracts_project_id", ["project_id"], False),
        ("ix_project_contracts_owner", ["owner_type", "owner_id"], False),
        ("ix_project_contracts_owner_id", ["owner_id"], False),
        ("ix_project_contracts_owner_type", ["owner_type"], False),
        ("ix_project_contracts_company_id", ["company_id"], False),
        ("ix_project_contracts_created_by_id", ["created_by_id"], False),
        ("ix_project_contracts_status", ["status"], False),
        ("ix_project_contracts_share_token", ["share_token"], True),
    ]
    for index_name, columns, unique in indexes:
        if not has_index("project_contracts", index_name, schema):
            op.create_index(index_name, "project_contracts", columns, unique=unique, schema=schema)

    if not has_constraint("project_contracts", "uq_project_contracts_project_id", schema):
        op.create_unique_constraint(
            "uq_project_contracts_project_id",
            "project_contracts",
            ["project_id"],
            schema=schema,
        )


def downgrade() -> None:
    schema = Base.metadata.schema
    if has_table("project_contracts", schema):
        op.drop_table("project_contracts", schema=schema)
