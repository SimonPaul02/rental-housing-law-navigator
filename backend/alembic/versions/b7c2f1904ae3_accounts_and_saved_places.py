"""accounts: users and saved places

WorkOS holds the credentials; these two tables hold what WorkOS cannot. `users` carries the
role a person signed up as - with no organisations in the picture an AuthKit access token
has no `role` claim to read it from - and `saved_places` the buildings they watch. Both are
keyed by the WorkOS user id, which is the `sub` of a verified access token and the only
identity this API trusts.

Revision ID: b7c2f1904ae3
Revises: a1b443d62853
Create Date: 2026-10-04 02:10:00.000000
"""

import sqlalchemy as sa

from alembic import op

revision = "b7c2f1904ae3"
down_revision = "a1b443d62853"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("workos_user_id", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("picture_url", sa.Text(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.PrimaryKeyConstraint("workos_user_id"),
    )
    op.create_index(op.f("ix_users_role"), "users", ["role"], unique=False)

    op.create_table(
        "saved_places",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("address_id", sa.String(length=16), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(["owner_id"], ["users.workos_user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        # One row per building per owner: saving the same address twice is the same fact,
        # so the API returns the existing row instead of making a duplicate.
        sa.UniqueConstraint("owner_id", "address_id", name="uq_saved_place_owner_address"),
    )
    op.create_index(op.f("ix_saved_places_owner_id"), "saved_places", ["owner_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_saved_places_owner_id"), table_name="saved_places")
    op.drop_table("saved_places")
    op.drop_index(op.f("ix_users_role"), table_name="users")
    op.drop_table("users")
