"""Widen the lookup result to the submission vocabulary

The column was sized for `applies`, `does_not_apply` and `unknown`. The
challenge's vocabulary adds `superseded`, `pending` and `not_yet_effective`,
and the last of those is seventeen characters, so persisting a lookup failed
with `value too long for type character varying(16)`.

Revision ID: 1a17b02de398
Revises: 05f0ce8cf997
Create Date: 2026-10-04 03:05:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "1a17b02de398"
down_revision = "05f0ce8cf997"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "lookups",
        "result",
        existing_type=sa.String(length=16),
        type_=sa.String(length=32),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "lookups",
        "result",
        existing_type=sa.String(length=32),
        type_=sa.String(length=16),
        existing_nullable=False,
    )
