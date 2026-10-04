"""Module A payloads. RuleRecord mirrors schema/rule_record.schema.json."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Level(StrEnum):
    state = "state"
    city = "city"


class Category(StrEnum):
    rent_increase_limits = "rent_increase_limits"
    just_cause_eviction = "just_cause_eviction"
    security_deposits = "security_deposits"
    application_screening_fees = "application_screening_fees"
    screening_restrictions = "screening_restrictions"
    algorithmic_rent_setting = "algorithmic_rent_setting"


class RuleStatus(StrEnum):
    in_force = "in_force"
    not_yet_effective = "not_yet_effective"
    pending = "pending"
    failed = "failed"


class ExtractedRule(BaseModel):
    """What the model returns per rule.

    No team_rule_id here - the service assigns ids so they stay unique and
    deterministic across a run rather than being invented by the model.
    """

    model_config = ConfigDict(extra="forbid")

    jurisdiction: str = Field(
        description="State code ('CA','NJ','MA') or 'City, ST' e.g. 'San Francisco, CA'."
    )
    level: Level
    category: Category
    status: RuleStatus = Field(
        description=(
            "Status as of the query date. An enacted law with a future effective "
            "date is 'not_yet_effective'. A bill still in the legislature is "
            "'pending'. A measure that was defeated or struck is 'failed'."
        )
    )
    title: str
    requirement: str = Field(description="One or two plain-language sentences.")
    key_value: str | None = Field(
        default=None,
        description="Headline number or formula, e.g. '1.5 months rent', 'lesser of CPI or 4%'.",
    )
    coverage_conditions: str | None = Field(
        default=None,
        description=(
            "Who/what is covered: year-built or certificate-of-occupancy cutoffs, "
            "unit counts, owner type. State the cutoff precisely."
        ),
    )
    exemptions: str | None = None
    interaction: str | None = Field(
        default=None,
        description="How this rule interacts with or yields to another rule.",
    )
    effective_date: str | None = Field(
        default=None, description="YYYY, YYYY-MM or YYYY-MM-DD. Null if not stated."
    )
    citation: str = Field(description="Official cite, e.g. 'Cal. Civ. Code § 1947.12'.")
    quoted_span: str = Field(
        min_length=20,
        description=(
            "Exact verbatim text copied from the supplied document that supports "
            "this rule. Must appear in the document character-for-character."
        ),
    )
    confidence: float = Field(ge=0.0, le=1.0)


class DocumentExtraction(BaseModel):
    """Top-level structured output for one document."""

    model_config = ConfigDict(extra="forbid")

    rules: list[ExtractedRule] = Field(
        description="Every in-scope rule in this document. Empty list if none."
    )
    document_note: str | None = Field(
        default=None,
        description="Why the document yielded no rules, or any caveat worth recording.",
    )


class RuleRecord(BaseModel):
    """A stored rule record - the submission shape, all schema fields present."""

    model_config = ConfigDict(from_attributes=True)

    team_rule_id: str
    jurisdiction: str
    level: str
    category: str
    status: str
    title: str
    requirement: str
    key_value: str | None = None
    coverage_conditions: dict | str | None = None
    exemptions: str | None = None
    overrides: list[str] = Field(default_factory=list)
    interaction: str | None = None
    effective_date: str | None = None
    citation: str
    source_doc_id: str | None = None
    source_url: str
    quoted_span: str
    confidence: float | None = None
    conflict_flag: bool = False
    conflict_note: str | None = None


class DocumentSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    doc_id: str
    jurisdictions: str
    url: str
    source_type: str | None = None
    capture: str | None = None
    status: str | None = None
    text_file: str | None = None
    has_text: bool = False
    rule_count: int = 0


class DocumentDetail(DocumentSummary):
    body: str | None = None
    retrieved_at: str | None = None
    sha256: str | None = None


class ExtractRequest(BaseModel):
    doc_ids: list[str] | None = Field(
        default=None, description="Omit to extract every document that has text."
    )
    replace: bool = Field(default=True, description="Delete this document's existing rules first.")


class RuleImportRequest(BaseModel):
    """A rules.json payload from an extraction pass run somewhere else.

    This is how a deployment receives the result of a pass without having to
    re-run the model against it: extraction costs money and does not repeat
    itself exactly, so a submission reviewed in one place should be the one
    that is served, not a fresh approximation of it.
    """

    rules: list[RuleRecord]
    replace: bool = Field(
        default=False,
        description=(
            "Delete every existing rule first. Use it when the payload is a whole "
            "corpus pass; leave it off to add to or update what is already there."
        ),
    )


class RuleImportRejection(BaseModel):
    team_rule_id: str
    reason: str


class RuleImportResult(BaseModel):
    received: int
    inserted: int
    updated: int
    deleted: int = Field(description="Existing rules removed because `replace` was set.")
    rejected: list[RuleImportRejection]
    total_after: int


class RunSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    run_id: str
    status: str
    model: str | None = None
    docs_total: int
    docs_done: int
    docs_failed: int
    rules_extracted: int
    input_tokens: int
    output_tokens: int
    error: str | None = None


class ExtractDocResult(BaseModel):
    doc_id: str
    rules: list[RuleRecord]
    document_note: str | None = None
    spans_verified: int
    spans_rejected: int
    input_tokens: int
    output_tokens: int


class RuleStats(BaseModel):
    total: int
    by_category: dict[str, int]
    by_status: dict[str, int]
    by_level: dict[str, int]
    by_jurisdiction: dict[str, int]
    documents_total: int
    documents_with_text: int
    documents_extracted: int
    flagged_conflicts: int


ResultLiteral = Literal["applies", "does_not_apply", "unknown"]
