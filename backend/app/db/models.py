"""SQLAlchemy ORM models.

Mirrors schema/rule_record.schema.json for rules, plus the tables Modules B
and C need to record jurisdiction resolution, per-address lookups and
change-test results.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# --------------------------------------------------------------------------
# Corpus (shared by Module A)
# --------------------------------------------------------------------------
class Document(Base, TimestampMixin):
    """One row per corpus_manifest.csv entry."""

    __tablename__ = "documents"

    doc_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    jurisdictions: Mapped[str] = mapped_column(String(128), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str | None] = mapped_column(String(64))
    capture: Mapped[str | None] = mapped_column(String(32))
    retrieved_at: Mapped[str | None] = mapped_column(String(32))
    sha256: Mapped[str | None] = mapped_column(String(64))
    text_file: Mapped[str | None] = mapped_column(String(128))
    # Free-form: "ok", "link-only", or a full capture error such as
    # "manual: 403 Client Error: Forbidden for url: ..." - so not a VARCHAR.
    status: Mapped[str | None] = mapped_column(Text)
    # Full text, loaded from corpus/text/. NULL for link-only sources.
    body: Mapped[str | None] = mapped_column(Text)

    # --- pipeline classification (see pipeline/inventory.py) ---
    # law_text | bill | official_guide | code_mirror | secondary
    doc_type: Mapped[str | None] = mapped_column(String(24), index=True)
    # Trust tier 1, 1b, 2, 3, 4 - drives confidence and merge precedence.
    tier: Mapped[str | None] = mapped_column(String(4), index=True)
    # provided | fetched | organizer_release
    origin: Mapped[str] = mapped_column(String(24), default="provided", nullable=False)
    # sha256 of body; the extraction cache keys on it, so an unchanged
    # document never costs a second API call.
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    doc_type_reason: Mapped[str | None] = mapped_column(Text)
    # Set when a human has confirmed the classification.
    doc_type_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    rules: Mapped[list[Rule]] = relationship(back_populates="document")

    @property
    def has_text(self) -> bool:
        return bool(self.body)


# --------------------------------------------------------------------------
# Module A - rule extraction
# --------------------------------------------------------------------------
class Rule(Base, TimestampMixin):
    """A rule record. Field names match the challenge JSON schema exactly."""

    __tablename__ = "rules"

    team_rule_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    jurisdiction: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    level: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    requirement: Mapped[str] = mapped_column(Text, nullable=False)
    key_value: Mapped[str | None] = mapped_column(Text)
    # schema allows string | object | null
    coverage_conditions: Mapped[dict | str | None] = mapped_column(JSONB)
    exemptions: Mapped[str | None] = mapped_column(Text)
    overrides: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    interaction: Mapped[str | None] = mapped_column(Text)
    effective_date: Mapped[str | None] = mapped_column(String(10))
    citation: Mapped[str] = mapped_column(Text, nullable=False)
    source_doc_id: Mapped[str | None] = mapped_column(
        ForeignKey("documents.doc_id", ondelete="SET NULL"), index=True
    )
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    quoted_span: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    conflict_flag: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    conflict_note: Mapped[str | None] = mapped_column(Text)

    # Provenance: which extraction run produced this record.
    run_id: Mapped[str | None] = mapped_column(String(36), index=True)

    # --- merge provenance (see pipeline/merge.py) ---
    # jurisdiction|category|canonical citation - team_rule_id hashes this, so
    # ids stay stable across runs and lookups.json keeps pointing at the same
    # rule.
    group_key: Mapped[str | None] = mapped_column(Text, index=True)
    # Tier of the MAIN source this rule's fields came from.
    tier: Mapped[str | None] = mapped_column(String(4))
    # [{doc_id, tier, agrees, fields}] - every other source for the same law.
    other_sources: Mapped[list[dict]] = mapped_column(JSONB, default=list, nullable=False)
    # none | text | section | url - how the citation was verified.
    citation_evidence: Mapped[str | None] = mapped_column(String(16))

    document: Mapped[Document | None] = relationship(back_populates="rules")


class ExtractionRun(Base, TimestampMixin):
    """One Module A pipeline invocation, so the demo can show progress."""

    __tablename__ = "extraction_runs"

    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), default="running", nullable=False)
    model: Mapped[str | None] = mapped_column(String(64))
    doc_ids: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    docs_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    docs_done: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    docs_failed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rules_extracted: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    # Append-only log the SSE endpoint replays: [{ts, doc_id, event, detail}]
    events: Mapped[list[dict]] = mapped_column(JSONB, default=list, nullable=False)


# --------------------------------------------------------------------------
# Module B - address lookup
# --------------------------------------------------------------------------
class Address(Base, TimestampMixin):
    """One row per data/sample_addresses.csv entry."""

    __tablename__ = "addresses"

    address_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    street_address: Mapped[str] = mapped_column(Text, nullable=False)
    postal_city: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    state: Mapped[str] = mapped_column(String(2), nullable=False, index=True)
    zip: Mapped[str | None] = mapped_column(String(10))
    year_built: Mapped[int | None] = mapped_column(Integer)
    units: Mapped[int | None] = mapped_column(Integer)
    use_code: Mapped[str | None] = mapped_column(String(32))
    use_description: Mapped[str | None] = mapped_column(Text)
    source_dataset: Mapped[str | None] = mapped_column(Text)
    retrieved_at: Mapped[str | None] = mapped_column(String(32))

    jurisdiction: Mapped[AddressJurisdiction | None] = relationship(
        back_populates="address", uselist=False, cascade="all, delete-orphan"
    )


class AddressJurisdiction(Base, TimestampMixin):
    """Resolved legal jurisdiction. postal_city is the mailing city and is
    not always the legal city ("Van Nuys" -> City of Los Angeles)."""

    __tablename__ = "address_jurisdictions"

    address_id: Mapped[str] = mapped_column(
        ForeignKey("addresses.address_id", ondelete="CASCADE"), primary_key=True
    )
    legal_city: Mapped[str | None] = mapped_column(String(128), index=True)
    legal_state: Mapped[str | None] = mapped_column(String(2), index=True)
    county: Mapped[str | None] = mapped_column(String(128))
    # "census" | "postal_fallback" | "manual"
    method: Mapped[str] = mapped_column(String(32), nullable=False)
    matched_address: Mapped[str | None] = mapped_column(Text)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    place_geoid: Mapped[str | None] = mapped_column(String(16))
    confidence: Mapped[float | None] = mapped_column(Float)
    note: Mapped[str | None] = mapped_column(Text)

    address: Mapped[Address] = relationship(back_populates="jurisdiction")


class Lookup(Base, TimestampMixin):
    """Module B output: does rule X apply to address Y on date Z?"""

    __tablename__ = "lookups"
    __table_args__ = (UniqueConstraint("address_id", "team_rule_id", "as_of", name="uq_lookup"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    address_id: Mapped[str] = mapped_column(
        ForeignKey("addresses.address_id", ondelete="CASCADE"), index=True
    )
    team_rule_id: Mapped[str] = mapped_column(
        ForeignKey("rules.team_rule_id", ondelete="CASCADE"), index=True
    )
    as_of: Mapped[dt.date] = mapped_column(Date, nullable=False, index=True)
    # "applies" | "does_not_apply" | "unknown"
    result: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    conflict_flag: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Which coverage condition could not be evaluated, for "unknown".
    unresolved_fields: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)


# --------------------------------------------------------------------------
# Module C - change tracking
# --------------------------------------------------------------------------
class ChangeResult(Base, TimestampMixin):
    """Result of one change test (T1-T5) from dev/change_tests.json."""

    __tablename__ = "change_results"
    __table_args__ = (UniqueConstraint("test_id", "as_of", name="uq_change_result"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    test_id: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    as_of: Mapped[dt.date] = mapped_column(Date, nullable=False)
    test_type: Mapped[str | None] = mapped_column(String(24))
    affected_address_ids: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    conflict_flag_address_ids: Mapped[list[str]] = mapped_column(
        JSONB, default=list, nullable=False
    )
    notes: Mapped[str | None] = mapped_column(Text)
    # Per-rule before/after status for "as_of" tests.
    detail: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)


# --------------------------------------------------------------------------
# Pipeline support
# --------------------------------------------------------------------------
class AuditEntry(Base):
    """Append-only audit trail. Never updated, never deleted."""

    __tablename__ = "audit_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    run_id: Mapped[str | None] = mapped_column(String(36), index=True)
    channel: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)


class ExtractionCache(Base, TimestampMixin):
    """Model output keyed by (document content, prompt version, model).

    This is what makes a rerun reproducible. Temperature is not a knob on
    current models - it is removed and rejected - so determinism comes from
    replaying cached output, not from the sampler.
    """

    __tablename__ = "extraction_cache"

    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    doc_id: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    response: Mapped[dict] = mapped_column(JSONB, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
