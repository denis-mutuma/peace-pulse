"""initial production schema

Revision ID: 20260513_0001
Revises:
Create Date: 2026-05-13
"""

from __future__ import annotations

from alembic import op
from sqlalchemy.schema import CreateTable

from services.api_prod.db import Base
from services.api_prod import models  # noqa: F401


revision = "20260513_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in Base.metadata.sorted_tables:
        op.execute(CreateTable(table))


def downgrade() -> None:
    for table in reversed(Base.metadata.sorted_tables):
        op.drop_table(table.name)
