"""Initial Pan Majster schema.

Revision ID: 20260611_01
Revises:
Create Date: 2026-06-11
"""

from alembic import op
from sqlalchemy import text

from panmajster.db import Base
from panmajster import models  # noqa: F401


revision = "20260611_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    if Base.metadata.schema:
        quoted = connection.dialect.identifier_preparer.quote(Base.metadata.schema)
        connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {quoted}"))
    Base.metadata.create_all(bind=connection)


def downgrade() -> None:
    connection = op.get_bind()
    Base.metadata.drop_all(bind=connection)
    if Base.metadata.schema:
        quoted = connection.dialect.identifier_preparer.quote(Base.metadata.schema)
        connection.execute(text(f"DROP SCHEMA IF EXISTS {quoted}"))
