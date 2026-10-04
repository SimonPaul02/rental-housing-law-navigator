"""Persist the extraction note per document

The model already explains itself per document - and in particular why a
document yielded no rules - but the note only ever reached the HTTP response
and was dropped. Storing it makes a 0 in the corpus table readable: "nothing
here is in scope" and "the capture lost the statute body" are different
answers and should not look the same.

Revision ID: 05f0ce8cf997
Revises: c742f3a964e1
Create Date: 2026-10-04 03:24:54.404200
"""

import sqlalchemy as sa
from alembic import op

revision = "05f0ce8cf997"
down_revision = "c742f3a964e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("document_note", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "document_note")
