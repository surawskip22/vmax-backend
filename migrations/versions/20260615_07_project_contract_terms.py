"""Add planned dates and contract amount for step 5D.

Revision ID: 20260615_07
Revises: 20260615_06
Create Date: 2026-06-15
"""

from alembic import op
import sqlalchemy as sa

from panmajster.db import Base


revision = "20260615_07"
down_revision = "20260615_06"
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
    columns = [
        ("planned_start_date", sa.Column("planned_start_date", sa.Date(), nullable=True)),
        ("planned_end_date", sa.Column("planned_end_date", sa.Date(), nullable=True)),
        (
            "schedule_uncertainty_days",
            sa.Column("schedule_uncertainty_days", sa.Integer(), nullable=True),
        ),
        ("contract_amount", sa.Column("contract_amount", sa.Numeric(12, 2), nullable=True)),
        ("contract_currency", sa.Column("contract_currency", sa.String(3), nullable=True)),
    ]
    for column_name, column in columns:
        if not has_column("projects", column_name, schema):
            op.add_column("projects", column, schema=schema)


def downgrade() -> None:
    schema = Base.metadata.schema
    for column_name in [
        "contract_currency",
        "contract_amount",
        "schedule_uncertainty_days",
        "planned_end_date",
        "planned_start_date",
    ]:
        if has_column("projects", column_name, schema):
            op.drop_column("projects", column_name, schema=schema)
