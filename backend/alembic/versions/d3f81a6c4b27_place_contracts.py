"""place_contracts: the tenancy agreement for a saved building

The first table in this database that holds a file, and the most private one in it. The
bytes live in the row rather than behind a path or a bucket key, because there is no object
store in this deployment and a second home for the data would be a second thing to keep in
step - and a second way for a deleted lease to survive its own deletion.

`unit_label` is what lets one building carry many agreements. A renter has one lease for one
home; a thirty-two unit building has thirty-two, and the unit is the only thing that
distinguishes them.

Revision ID: d3f81a6c4b27
Revises: 1a17b02de398
Create Date: 2026-10-04 11:40:00.000000
"""

import sqlalchemy as sa

from alembic import op

revision = "d3f81a6c4b27"
down_revision = "1a17b02de398"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "place_contracts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        # Denormalised from saved_places on purpose: every read filters on it directly, so
        # a mistake in a join cannot widen the query past its owner.
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("place_id", sa.Integer(), nullable=False),
        sa.Column("unit_label", sa.String(length=64), nullable=True),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("starts_on", sa.Date(), nullable=True),
        sa.Column("ends_on", sa.Date(), nullable=True),
        sa.Column("monthly_rent_cents", sa.Integer(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.workos_user_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["place_id"], ["saved_places.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_place_contracts_owner_id"), "place_contracts", ["owner_id"], unique=False
    )
    op.create_index(
        op.f("ix_place_contracts_place_id"), "place_contracts", ["place_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_place_contracts_place_id"), table_name="place_contracts")
    op.drop_index(op.f("ix_place_contracts_owner_id"), table_name="place_contracts")
    op.drop_table("place_contracts")
