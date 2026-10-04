"""Store source-backed ZIP review decisions independently of geocoding.

Revision ID: c742f3a964e1
Revises: b61c83e14290
"""

import sqlalchemy as sa

from alembic import op

revision = "c742f3a964e1"
down_revision = "b61c83e14290"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "address_zip_reviews",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("address_id", sa.String(length=16), nullable=False),
        sa.Column("input_street_address", sa.Text(), nullable=False),
        sa.Column("input_postal_city", sa.String(length=128), nullable=False),
        sa.Column("input_state", sa.String(length=2), nullable=False),
        sa.Column("input_zip", sa.String(length=10), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("confirmed_zip", sa.String(length=10), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("reviewer", sa.String(length=128), nullable=False),
        sa.Column("reviewed_at", sa.Date(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.ForeignKeyConstraint(["address_id"], ["addresses.address_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_address_zip_reviews_address_id"), "address_zip_reviews", ["address_id"]
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_address_zip_reviews_address_id"), table_name="address_zip_reviews")
    op.drop_table("address_zip_reviews")
