"""Persist Module B property-fact and jurisdiction evidence.

Revision ID: b61c83e14290
Revises: b7c2f1904ae3
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "b61c83e14290"
down_revision = "b7c2f1904ae3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("addresses", sa.Column("property_facts", postgresql.JSONB(), nullable=True))
    op.add_column(
        "address_jurisdictions",
        sa.Column("resolution_evidence", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("address_jurisdictions", "resolution_evidence")
    op.drop_column("addresses", "property_facts")
