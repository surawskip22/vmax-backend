"""Initial Pan Majster schema.

Revision ID: 20260611_01
Revises:
Create Date: 2026-06-11
"""

from alembic import op

from panmajster.db import Base
from panmajster import models  # noqa: F401


revision = "20260611_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
