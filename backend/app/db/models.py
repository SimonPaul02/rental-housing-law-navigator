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
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
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
    # What the model said about this document on the last extraction, and in
    # particular why it yielded no rules. Without it a 0 in the corpus table
    # is unreadable: "nothing here is in scope" and "the capture lost the
    # statute body" look identical, and telling them apart meant going back
    # to the source text by hand. Written by extraction, not by the manifest,
    # so re-seeding the corpus leaves it alone.
    document_note: Mapped[str | None] = mapped_column(Text)

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
    """A building: one the account was set up with, or one somebody typed in.

    Told apart by `imported`. Both are ordinary addresses and are evaluated by
    the same code; what differs is whether they count towards anything this
    app reports."""

    __tablename__ = "addresses"

    # `A0001` for an imported row; `U` + 12 hex for one somebody typed, which
    # is not guessable. That matters because `/lookup/{id}` answers for both:
    # the answer carries no street address, but a sequential id would still let
    # somebody walk the list of places people live.
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
    # Evidence-bearing normalized facts; raw assessor values and conflicts survive seeding.
    property_facts: Mapped[dict | None] = mapped_column(JSONB)
    # True for the address book the account came with, false for one somebody
    # typed in afterwards.
    #
    # This is the line between the records an account is answerable for and a
    # building one person went and added, and it is load-bearing in both
    # directions. Every figure this app *reports* is about the import - it is
    # the agency's denominator, the extent of lookups.json and the set the
    # change cases scan - so every one of those queries filters on it, and
    # `test_a_typed_address_is_counted_nowhere` is what keeps a missed call
    # site from quietly moving a denominator. The per-address lookups
    # deliberately do *not* filter, because a renter's own home has to be
    # answerable exactly as an imported row is.
    imported: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true"), index=True
    )

    jurisdiction: Mapped[AddressJurisdiction | None] = relationship(
        back_populates="address", uselist=False, cascade="all, delete-orphan"
    )
    zip_reviews: Mapped[list[AddressZipReview]] = relationship(
        back_populates="address", cascade="all, delete-orphan"
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
    # "geocoder" | "review_override" | "unresolved"
    method: Mapped[str] = mapped_column(String(32), nullable=False)
    matched_address: Mapped[str | None] = mapped_column(Text)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    place_geoid: Mapped[str | None] = mapped_column(String(16))
    confidence: Mapped[float | None] = mapped_column(Float)
    note: Mapped[str | None] = mapped_column(Text)
    resolution_evidence: Mapped[dict | None] = mapped_column(JSONB)

    address: Mapped[Address] = relationship(back_populates="jurisdiction")

    @property
    def zip_assessment(self) -> dict | None:
        """Structured postal evidence; the jurisdiction columns remain independent."""
        return (self.resolution_evidence or {}).get("zip_assessment")


class AddressZipReview(Base, TimestampMixin):
    """Append-only, source-backed human finding kept across resolver reruns."""

    __tablename__ = "address_zip_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    address_id: Mapped[str] = mapped_column(
        ForeignKey("addresses.address_id", ondelete="CASCADE"), index=True, nullable=False
    )
    input_street_address: Mapped[str] = mapped_column(Text, nullable=False)
    input_postal_city: Mapped[str] = mapped_column(String(128), nullable=False)
    input_state: Mapped[str] = mapped_column(String(2), nullable=False)
    input_zip: Mapped[str] = mapped_column(String(10), nullable=False)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    confirmed_zip: Mapped[str | None] = mapped_column(String(10))
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    reviewer: Mapped[str] = mapped_column(String(128), nullable=False)
    reviewed_at: Mapped[dt.date] = mapped_column(Date, nullable=False)

    address: Mapped[Address] = relationship(back_populates="zip_reviews")


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
    # The submission vocabulary is wider than the old three values:
    # "not_yet_effective" alone is seventeen characters.
    result: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
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


# --------------------------------------------------------------------------
# Accounts
# --------------------------------------------------------------------------
class User(Base, TimestampMixin):
    """One row per person who has signed in and chosen a role.

    WorkOS is the identity provider and the only place a credential exists; this row is not
    a copy of the account, it is what the account is *for* here. It holds the one fact WorkOS
    cannot hold for us - which of the four roles a person signed up as - because with no
    organisations in the picture an AuthKit access token carries no `role` claim, and the
    role is what decides which app a person gets.

    `workos_user_id` is the verified token's `sub` and nothing else. The profile columns are
    a cache of the WorkOS user so a listing need not call WorkOS per row; they are written
    from the sealed session server-side, never from a browser payload.
    """

    __tablename__ = "users"

    workos_user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # renter | provider | agency | advocate - see modules/accounts/schemas.py
    role: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    name: Mapped[str | None] = mapped_column(Text)
    picture_url: Mapped[str | None] = mapped_column(Text)
    last_seen_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    places: Mapped[list[SavedPlace]] = relationship(
        back_populates="owner", cascade="all, delete-orphan"
    )


class SavedPlace(Base, TimestampMixin):
    """A building one person is watching - their home, or one of their properties.

    Private to its owner, with no sharing and no visibility between accounts: there is no
    column here that could point at another user, which is the point rather than an
    omission. The owner's role decides what the place is *called* in the interface ("my
    home", "a property"), not who may read it.
    """

    __tablename__ = "saved_places"
    __table_args__ = (
        UniqueConstraint("owner_id", "address_id", name="uq_saved_place_owner_address"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("users.workos_user_id", ondelete="CASCADE"), nullable=False, index=True
    )
    address_id: Mapped[str] = mapped_column(
        ForeignKey("addresses.address_id", ondelete="CASCADE"), nullable=False
    )
    # What the owner calls it. Optional - the street address is already a name.
    label: Mapped[str | None] = mapped_column(String(120))
    note: Mapped[str | None] = mapped_column(Text)

    owner: Mapped[User] = relationship(back_populates="places")
    address: Mapped[Address] = relationship()
    contracts: Mapped[list[PlaceContract]] = relationship(
        back_populates="place", cascade="all, delete-orphan", order_by="PlaceContract.id"
    )


class PlaceContract(Base, TimestampMixin):
    """A tenancy agreement somebody uploaded for one of their own buildings.

    The most private row in this database, and the only one holding a file. Three
    consequences follow, and all three are deliberate:

    `owner_id` is stored alongside `place_id` even though the place already knows its
    owner. Every read filters on it directly, so a mistake in a join cannot widen the
    query - the owner check does not depend on the relationship being written correctly.

    The bytes live in the column. There is no object store in this deployment to put them
    in, and a lease is a few hundred kilobytes; a filesystem path would be a second place
    for the data to be, with its own lifecycle and its own way of outliving the row it
    belongs to. `DELETE` here is the whole deletion.

    `unit_label` is what makes one building hold many agreements. A renter has one lease
    for one home; a provider with a thirty-two unit building has thirty-two, and the unit
    is the only thing that tells them apart. The rent and the term are optional and
    self-reported - nothing in this system verifies them, and nothing computes an
    entitlement from them. They are here so a figure can be read next to the rule that
    governs it, which is a person's job and not the evaluator's.
    """

    __tablename__ = "place_contracts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("users.workos_user_id", ondelete="CASCADE"), nullable=False, index=True
    )
    place_id: Mapped[int] = mapped_column(
        ForeignKey("saved_places.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Which unit this agreement is for. NULL means the building itself - a single-family
    # let, or a renter who has one home and no reason to name a unit.
    unit_label: Mapped[str | None] = mapped_column(String(64))
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer)
    # Self-reported, never verified, never used in a calculation.
    starts_on: Mapped[dt.date | None] = mapped_column(Date)
    ends_on: Mapped[dt.date | None] = mapped_column(Date)
    monthly_rent_cents: Mapped[int | None] = mapped_column(Integer)
    note: Mapped[str | None] = mapped_column(Text)
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)

    place: Mapped[SavedPlace] = relationship(back_populates="contracts")
