"""Separate the addresses an account came with from one somebody typed in.

The address book is imported: an account arrives already holding the buildings
it is answerable for, and everything this app reports is *about that import* -
it is the denominator on the agency's page, the extent of lookups.json, and
the set the change cases scan. A renter who types the building they live in
needs it evaluated and saved, and it must not join any of those. One table
holds both, so the table says which is which and every roll-up asks.

Default `true` because every row that exists when this runs came from the
import.

Revision ID: e2a7c3f10b45
Revises: d3f81a6c4b27
"""

import sqlalchemy as sa

from alembic import op

revision = "e2a7c3f10b45"
down_revision = "d3f81a6c4b27"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "addresses",
        sa.Column("imported", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    # Every roll-up filters on this, so it is worth an index even on a small
    # table: the handful of typed rows is the part that grows.
    op.create_index("ix_addresses_imported", "addresses", ["imported"])


def downgrade() -> None:
    op.drop_index("ix_addresses_imported", table_name="addresses")
    op.drop_column("addresses", "imported")
