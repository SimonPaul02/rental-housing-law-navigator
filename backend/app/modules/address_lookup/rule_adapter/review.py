"""Where compiled revisions and review decisions live.

Kept separate from Module A's raw rule on purpose: re-extraction rewrites the
rule table, and an approved interpretation of legal text is work that must not
be erased by it. Approval is bound to rule, source-content, and compiler
versions, so changed wording or a changed document invalidates it.

A JSON file is the store for now. Nothing above this module knows that - the
evaluator and its tests take objects - so moving these two documents into
`compiled_rule_revisions` and `rule_compilation_reviews` tables later is a
change to this file only.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.modules.address_lookup.rule_adapter.models import (
    CLASSIFIER_VERSION,
    COMPILER_VERSION,
    Atom,
    Basis,
    ClassifiedClause,
    CompiledRule,
    CoverageBasis,
    EffectiveDate,
    Expr,
    Field,
    InvalidCompilation,
    Op,
    Origin,
    Qualification,
    Relation,
    RelationType,
    ReviewState,
    SourceAnchor,
    UnmappedClause,
    ValuePeriod,
    validate_expr,
)


# ------------------------------------------------------------ deserialising ---
def _anchor_from(payload: dict) -> SourceAnchor | None:
    span = payload.get("source_span")
    if not span:
        return None
    return SourceAnchor(
        span,
        payload.get("source_doc_id"),
        payload.get("source_url"),
        payload.get("clause_id"),
        Origin(payload["origin"]) if payload.get("origin") else None,
        payload.get("source_hash"),
    )


def atom_from_json(payload: dict) -> Atom:
    field_name = Field(payload["field"])
    value = payload.get("value")
    if field_name is Field.certificate_of_occupancy_date and isinstance(value, str):
        value = dt.date.fromisoformat(value)
    return Atom(
        id=payload["id"],
        field=field_name,
        op=Op(payload["op"]),
        value=value,
        anchor=_anchor_from(payload),
        note=payload.get("note"),
        basis=Basis(payload.get("basis", Basis.source_verified)),
    )


def _date_from(d: dict) -> EffectiveDate:
    return EffectiveDate(
        d["raw"],
        dt.date.fromisoformat(d["earliest"]),
        dt.date.fromisoformat(d["latest"]),
        d["precision"],
    )


def expr_from_json(payload: dict) -> Expr:
    if not isinstance(payload, dict) or len(payload) != 1 or not ({"all", "any"} & payload.keys()):
        raise InvalidCompilation("expression must contain exactly one all or any list")
    kind = "all" if "all" in payload else "any"
    if not isinstance(payload[kind], list):
        raise InvalidCompilation("expression children must be a list")
    children: list[Atom | Expr] = []
    for child in payload.get(kind, []):
        children.append(
            expr_from_json(child) if ("all" in child or "any" in child) else atom_from_json(child)
        )
    return Expr(kind, tuple(children))


def compiled_from_json(payload: dict) -> CompiledRule:
    period = payload.get("key_value_period")
    return CompiledRule(
        team_rule_id=payload["team_rule_id"],
        rule_version_hash=payload["rule_version_hash"],
        compiler_version=payload.get("compiler_version", "1"),
        review_state=ReviewState(payload.get("review_state", "needs_review")),
        coverage_basis=CoverageBasis(payload.get("coverage_basis", "unresolved")),
        source_hash=payload.get("source_hash"),
        source_doc_id=payload.get("source_doc_id"),
        level=payload.get("level", "state"),
        jurisdiction=payload.get("jurisdiction", ""),
        status=payload.get("status", "in_force"),
        effective_dates=tuple(_date_from(d) for d in payload.get("effective_dates", [])),
        effective_date_unresolved=payload.get("effective_date_unresolved", False),
        unverified_dates=tuple(_date_from(d) for d in payload.get("unverified_dates", [])),
        key_value_period=(
            ValuePeriod(
                dt.date.fromisoformat(period["start"]),
                dt.date.fromisoformat(period["end"]),
                _anchor_from(period["anchor"]),
            )
            if period
            else None
        ),
        coverage=expr_from_json(payload.get("coverage", {"all": []})),
        exemptions=expr_from_json(payload.get("exemptions", {"any": []})),
        unmapped_text=tuple(
            UnmappedClause(
                u["text"],
                Origin(u["origin"]),
                u["reason"],
                u.get("clause_id", ""),
                u.get("source_span"),
                u.get("source_doc_id"),
                u.get("source_hash"),
                u.get("proposal"),
            )
            for u in payload.get("unmapped_text", [])
        ),
        classified=tuple(
            ClassifiedClause(
                c["text"],
                Origin(c["origin"]),
                c.get("clause_id", ""),
                c["kind"],
                c.get("rationale", ""),
                c.get("source_span"),
            )
            for c in payload.get("classified", [])
        ),
        issue_key=payload.get("issue_key", ""),
        notes=tuple(payload.get("notes", [])),
        # A revision written before the classifier existed reads as stale.
        classifier_version=payload.get("classifier_version", "0"),
    )


def relation_from_json(payload: dict) -> Relation:
    # Old phrase-pattern approvals have no version-bound decision and are unsafe.
    reviewed = bool(
        payload.get("compiler_version") == COMPILER_VERSION
        and payload.get("qualification") in set(Qualification)
        and payload.get("left_evidence_span")
        and payload.get("right_evidence_span")
        and payload.get("reviewer")
        and payload.get("reviewed_at")
        and payload.get("left_version_hash")
        and payload.get("right_version_hash")
        and payload.get("left_source_hash")
        and payload.get("right_source_hash")
    )
    return Relation(
        left_rule_id=payload["left_rule_id"],
        right_rule_id=payload["right_rule_id"],
        issue_key=payload["issue_key"],
        relation=RelationType(payload["relation"]),
        anchor=_anchor_from(payload),
        condition=payload.get("condition"),
        review_state=ReviewState(payload.get("review_state", "needs_review"))
        if reviewed
        else ReviewState.needs_review,
        qualification=Qualification(payload.get("qualification", "unresolved"))
        if reviewed
        else Qualification.unresolved,
        valid_from=dt.date.fromisoformat(payload["valid_from"])
        if reviewed and payload.get("valid_from")
        else None,
        left_version_hash=payload.get("left_version_hash"),
        right_version_hash=payload.get("right_version_hash"),
        left_source_hash=payload.get("left_source_hash"),
        right_source_hash=payload.get("right_source_hash"),
        reviewer=payload.get("reviewer"),
        reviewed_at=payload.get("reviewed_at"),
        review_note=payload.get("review_note"),
        basis=payload.get("basis"),
    )


# -------------------------------------------------------------------- store ---
@dataclass
class ReviewStore:
    """Compiled revisions, relations, and the queue of what needs a human."""

    path: Path
    revisions: dict[str, dict] = field(default_factory=dict)
    relations: list[dict] = field(default_factory=list)
    reviews: dict[str, dict] = field(default_factory=dict)
    date_reviews: dict[str, dict] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> ReviewStore:
        if not path.exists():
            return cls(path=path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            path=path,
            revisions=payload.get("revisions", {}),
            relations=payload.get("relations", []),
            reviews=payload.get("reviews", {}),
            date_reviews=payload.get("date_reviews", {}),
        )

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {
                    "revisions": self.revisions,
                    "relations": self.relations,
                    "reviews": self.reviews,
                    "date_reviews": self.date_reviews,
                },
                indent=2,
                sort_keys=True,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

    # -- revisions ------------------------------------------------------------
    def put(self, compiled: CompiledRule) -> None:
        payload = compiled.to_json()
        old = self.revisions.get(compiled.team_rule_id, {})
        for key in ("legacy_review_state", "legacy_coverage_empty", "legacy_priority"):
            if key in old:
                payload[key] = old[key]
        self.revisions[compiled.team_rule_id] = payload

    def get(
        self, team_rule_id: str, rule_version_hash: str, source_hash: str | None = None
    ) -> CompiledRule | None:
        """The stored revision, but only if it describes the current text."""
        payload = self.revisions.get(team_rule_id)
        if (
            not payload
            or payload.get("rule_version_hash") != rule_version_hash
            or payload.get("compiler_version") != COMPILER_VERSION
            or payload.get("classifier_version") != CLASSIFIER_VERSION
            or payload.get("source_hash") != source_hash
        ):
            return None
        compiled = compiled_from_json(payload)
        if compiled.review_state is ReviewState.human_approved:
            decision = self.review_for(team_rule_id, rule_version_hash, source_hash)
            if not self._complete_approval(decision):
                compiled.review_state = ReviewState.needs_review
                if compiled.coverage.is_empty:
                    compiled.coverage_basis = CoverageBasis.unresolved
        return compiled

    @staticmethod
    def _complete_approval(decision: dict | None) -> bool:
        required = (
            "reviewer",
            "reviewed_at",
            "rationale",
            "resolved_clause_ids",
            "coverage",
            "exemptions",
            "coverage_basis",
        )
        return bool(
            decision
            and decision.get("decision") == "approve"
            and decision.get("review_state") == str(ReviewState.human_approved)
            and all(decision.get(key) is not None for key in required)
            and str(decision.get("reviewer", "")).strip()
            and str(decision.get("rationale", "")).strip()
            and (
                decision.get("coverage_basis") != str(CoverageBasis.explicit_unconditional)
                or decision.get("scope_evidence_span")
            )
        )

    def review_for(
        self, team_rule_id: str, rule_version_hash: str, source_hash: str | None = None
    ) -> dict | None:
        decision = self.reviews.get(team_rule_id)
        if (
            not decision
            or decision.get("rule_version_hash") != rule_version_hash
            or decision.get("source_hash") != source_hash
            or decision.get("compiler_version") != COMPILER_VERSION
        ):
            return None
        return decision

    def review_dates(
        self,
        compiled: CompiledRule,
        *,
        source_text: str,
        reviewer: str,
        rationale: str,
        effective_dates: list[dict],
        key_value_period: dict | None = None,
        role_evidence_span: str | None = None,
        evidence_doc_id: str | None = None,
        evidence_text: str | None = None,
        act_markers: tuple[str, ...] = (),
    ) -> None:
        """Resolve date roles using passages in the current source document.

        A rule's own source does not always state its effective date - AB 325's
        bill text gives only its approval - so a date may instead be quoted
        from another captured document (`evidence_doc_id`). That is accepted
        only when the same act is named in both: every `act_markers` entry
        (bill number, chapter) must occur in the quoted passage *and* in the
        rule's own source, so one bill's date cannot be pinned on another.
        The review stays bound to the rule's own version and source.
        """
        from app.modules.address_lookup.rule_adapter.compiler import (
            _SOURCE_DATE,
            _source_contains,
            _source_date,
            content_hash,
            parse_effective_date,
        )

        def names(text: str, marker: str) -> bool:
            return " ".join(marker.split()).casefold() in " ".join(text.split()).casefold()

        if evidence_doc_id:
            if not evidence_text or not act_markers:
                raise InvalidCompilation("evidence from another document needs its text and act")
            if not all(names(source_text, marker) for marker in act_markers):
                raise InvalidCompilation("the rule's own source does not name the same act")
        date_text = evidence_text if evidence_doc_id else source_text

        if (
            not reviewer.strip()
            or not rationale.strip()
            or compiled.compiler_version != COMPILER_VERSION
            or not compiled.source_hash
            or content_hash(source_text) != compiled.source_hash
        ):
            raise InvalidCompilation("current source, reviewer and rationale are required")
        if not effective_dates and compiled.effective_date_unresolved:
            role_evidence_span = role_evidence_span or (key_value_period or {}).get("source_span")
            if not role_evidence_span or not _source_contains(source_text, role_evidence_span):
                raise InvalidCompilation("clearing an ambiguous date needs source evidence")
        parsed: list[EffectiveDate] = []
        for item in effective_dates:
            date = parse_effective_date(item.get("raw"))
            span = item.get("source_span", "")
            supported = bool(
                date
                and (
                    date.raw in span
                    or any(
                        _source_date(match.group(0)) == date.earliest
                        for match in _SOURCE_DATE.finditer(span)
                    )
                )
            )
            # A quote from another document may cross a PDF line break, so its
            # whitespace is normalised; the rule's own source is matched exactly.
            contained = (
                " ".join(span.split()) in " ".join((evidence_text or "").split())
                if evidence_doc_id
                else _source_contains(date_text, span)
            )
            if not supported or not span.strip() or not contained:
                raise InvalidCompilation("effective date needs a current source passage")
            if evidence_doc_id and not all(names(span, marker) for marker in act_markers):
                raise InvalidCompilation("the quoted passage does not name the act")
            parsed.append(date)
        if key_value_period:
            try:
                start = dt.date.fromisoformat(key_value_period["start"])
                end = dt.date.fromisoformat(key_value_period["end"])
                span = key_value_period["source_span"]
            except (KeyError, ValueError, TypeError) as exc:
                raise InvalidCompilation("invalid key value period") from exc
            mentioned = {_source_date(match.group(0)) for match in _SOURCE_DATE.finditer(span)}
            if (
                start > end
                or not _source_contains(source_text, span)
                or not {start, end} <= mentioned
            ):
                raise InvalidCompilation(
                    "key value period needs an ordered, current source passage"
                )
        self.date_reviews[compiled.team_rule_id] = {
            "rule_version_hash": compiled.rule_version_hash,
            "source_hash": compiled.source_hash,
            "compiler_version": COMPILER_VERSION,
            "reviewer": reviewer,
            "reviewed_at": dt.datetime.now(dt.UTC).isoformat(),
            "rationale": rationale,
            "effective_dates": [d.to_json() for d in parsed],
            "effective_date_evidence": effective_dates,
            "role_evidence_span": role_evidence_span,
            "key_value_period": key_value_period,
            "evidence_doc_id": evidence_doc_id,
            "evidence_source_hash": content_hash(evidence_text) if evidence_doc_id else None,
            "act_markers": list(act_markers),
        }

    def date_review_for(
        self, team_rule_id: str, rule_version_hash: str, source_hash: str | None
    ) -> dict | None:
        decision = self.date_reviews.get(team_rule_id)
        if not decision or any(
            decision.get(key) != expected
            for key, expected in (
                ("rule_version_hash", rule_version_hash),
                ("source_hash", source_hash),
                ("compiler_version", COMPILER_VERSION),
            )
        ):
            return None
        return decision

    def apply_date_review(self, compiled: CompiledRule) -> CompiledRule:
        decision = self.date_review_for(
            compiled.team_rule_id, compiled.rule_version_hash, compiled.source_hash
        )
        if not decision:
            return compiled
        compiled.effective_dates = tuple(
            EffectiveDate(
                item["raw"],
                dt.date.fromisoformat(item["earliest"]),
                dt.date.fromisoformat(item["latest"]),
                item["precision"],
            )
            for item in decision["effective_dates"]
        )
        compiled.effective_date_unresolved = False
        compiled.unverified_dates = ()
        if period := decision.get("key_value_period"):
            compiled.key_value_period = ValuePeriod(
                dt.date.fromisoformat(period["start"]),
                dt.date.fromisoformat(period["end"]),
                SourceAnchor(
                    period["source_span"],
                    compiled.source_doc_id,
                    source_hash=compiled.source_hash,
                ),
            )
        compiled.notes = (
            *compiled.notes,
            f"dates reviewed by {decision['reviewer']}: {decision['rationale']}",
        )
        return compiled

    def review_coverage(
        self,
        compiled: CompiledRule,
        *,
        source_text: str,
        reviewer: str,
        rationale: str,
        resolved_clause_ids: list[str],
        decision: str = "approve",
        coverage: dict | None = None,
        exemptions: dict | None = None,
        coverage_basis: str | None = None,
        scope_evidence_span: str | None = None,
    ) -> None:
        """Record an explicit interpretation bound to rule, source and compiler."""
        from app.modules.address_lookup.rule_adapter.compiler import _source_contains, content_hash

        if not reviewer.strip() or not rationale.strip() or not compiled.source_hash:
            raise InvalidCompilation("reviewer, rationale and source text are required")
        if (
            content_hash(source_text) != compiled.source_hash
            or compiled.compiler_version != COMPILER_VERSION
        ):
            raise InvalidCompilation("source or compiler version changed; recompile before review")
        if decision not in ("approve", "hold", "reject"):
            raise InvalidCompilation("decision must be approve, hold, or reject")
        if decision != "approve":
            if resolved_clause_ids or coverage is not None or exemptions is not None:
                raise InvalidCompilation(
                    "hold/reject cannot resolve clauses or replace expressions"
                )
            self.reviews[compiled.team_rule_id] = {
                "rule_version_hash": compiled.rule_version_hash,
                "source_hash": compiled.source_hash,
                "compiler_version": COMPILER_VERSION,
                "decision": decision,
                "review_state": str(
                    ReviewState.needs_review if decision == "hold" else ReviewState.rejected
                ),
                "reviewer": reviewer,
                "reviewed_at": dt.datetime.now(dt.UTC).isoformat(),
                "rationale": rationale,
            }
            return
        available = {
            u.clause_id
            for u in compiled.unmapped_text
            if u.origin in (Origin.coverage, Origin.exemption)
        }
        if (
            len(set(resolved_clause_ids)) != len(resolved_clause_ids)
            or not set(resolved_clause_ids) <= available
        ):
            raise InvalidCompilation(
                "resolved clause IDs must name current pending coverage or exemptions"
            )
        parsed_coverage = expr_from_json(coverage) if coverage is not None else compiled.coverage
        parsed_exemptions = (
            expr_from_json(exemptions) if exemptions is not None else compiled.exemptions
        )
        if parsed_coverage.kind != "all" or parsed_exemptions.kind != "any":
            raise InvalidCompilation("coverage must be all; exemptions must be any")
        for expr in (parsed_coverage, parsed_exemptions):
            validate_expr(expr)
            for atom in expr.atoms():
                if (
                    not atom.anchor
                    or not _source_contains(source_text, atom.anchor.source_span)
                    or atom.anchor.source_hash != compiled.source_hash
                    or atom.anchor.source_doc_id != compiled.source_doc_id
                    or atom.anchor.clause_id is None
                ):
                    raise InvalidCompilation(
                        f"atom {atom.id} has no current document passage and clause"
                    )
        basis = CoverageBasis(coverage_basis) if coverage_basis else compiled.coverage_basis
        if parsed_coverage.is_empty and basis is CoverageBasis.conditions:
            raise InvalidCompilation("empty coverage cannot have conditions basis")
        if not parsed_coverage.is_empty and basis is CoverageBasis.explicit_unconditional:
            raise InvalidCompilation("unconditional basis requires empty coverage")
        if parsed_coverage.is_empty and basis is CoverageBasis.explicit_unconditional:
            if any("rso" in u.text.casefold() for u in compiled.unmapped_text):
                raise InvalidCompilation("RSO scope cannot be approved as unconditional coverage")
            if not scope_evidence_span or not _source_contains(source_text, scope_evidence_span):
                raise InvalidCompilation("unconditional scope needs a current source passage")
            coverage_ids = {
                u.clause_id for u in compiled.unmapped_text if u.origin is Origin.coverage
            }
            if not coverage_ids <= set(resolved_clause_ids):
                raise InvalidCompilation("all pending coverage clauses must be explicitly resolved")
        self.reviews[compiled.team_rule_id] = {
            "rule_version_hash": compiled.rule_version_hash,
            "source_hash": compiled.source_hash,
            "compiler_version": COMPILER_VERSION,
            "review_state": str(ReviewState.human_approved),
            "decision": decision,
            "reviewer": reviewer,
            "reviewed_at": dt.datetime.now(dt.UTC).isoformat(),
            "rationale": rationale,
            "resolved_clause_ids": resolved_clause_ids,
            "coverage": parsed_coverage.to_json(),
            "exemptions": parsed_exemptions.to_json(),
            "coverage_basis": str(basis),
            "scope_evidence_span": scope_evidence_span,
        }

    def apply_reviews(self, compiled: CompiledRule) -> CompiledRule:
        """Promote a revision a reviewer approved for exactly this text."""
        compiled = self.apply_date_review(compiled)
        decision = self.review_for(
            compiled.team_rule_id, compiled.rule_version_hash, compiled.source_hash
        )
        if (
            decision
            and decision.get("review_state") == str(ReviewState.rejected)
            and decision.get("decision") == "reject"
            and decision.get("reviewer")
            and decision.get("rationale")
            and decision.get("reviewed_at")
        ):
            compiled.review_state = ReviewState.rejected
            compiled.notes = (*compiled.notes, f"rejected in review: {decision['rationale']}")
        elif self._complete_approval(decision):
            try:
                coverage = expr_from_json(decision["coverage"])
                exemptions = expr_from_json(decision["exemptions"])
                basis = CoverageBasis(decision["coverage_basis"])
                validate_expr(coverage)
                validate_expr(exemptions)
                if coverage.kind != "all" or exemptions.kind != "any":
                    raise InvalidCompilation("reviewed expression has the wrong tree shape")
                if (coverage.is_empty and basis is CoverageBasis.conditions) or (
                    not coverage.is_empty and basis is CoverageBasis.explicit_unconditional
                ):
                    raise InvalidCompilation("reviewed coverage basis conflicts with expression")
                if (
                    coverage.is_empty
                    and basis is CoverageBasis.explicit_unconditional
                    and any("rso" in u.text.casefold() for u in compiled.unmapped_text)
                ):
                    raise InvalidCompilation("RSO scope cannot be unconditional")
                for atom in (*coverage.atoms(), *exemptions.atoms()):
                    if (
                        not atom.anchor
                        or atom.anchor.source_hash != compiled.source_hash
                        or atom.anchor.source_doc_id != compiled.source_doc_id
                        or atom.anchor.clause_id is None
                    ):
                        raise InvalidCompilation("reviewed atom has stale provenance")
            except (InvalidCompilation, ValueError, KeyError, TypeError):
                compiled.review_state = ReviewState.needs_review
                return compiled
            compiled.coverage = coverage
            compiled.exemptions = exemptions
            compiled.coverage_basis = basis
            resolved = set(decision["resolved_clause_ids"])
            compiled.unmapped_text = tuple(
                u for u in compiled.unmapped_text if u.clause_id not in resolved
            )
            compiled.review_state = (
                ReviewState.human_approved
                if not compiled.unmapped_text
                and compiled.coverage_basis is not CoverageBasis.unresolved
                else ReviewState.needs_review
            )
            compiled.notes = (
                *compiled.notes,
                f"coverage reviewed by {decision['reviewer']}: {decision['rationale']}",
            )
        return compiled

    # -- relations ------------------------------------------------------------
    def put_relations(self, relations: list[Relation]) -> None:
        old = {(r["left_rule_id"], r["right_rule_id"], r["issue_key"]): r for r in self.relations}
        current: list[dict] = []
        for rel in relations:
            payload = rel.to_json()
            previous = old.get((rel.left_rule_id, rel.right_rule_id, rel.issue_key))
            stable = (
                "left_version_hash",
                "right_version_hash",
                "left_source_hash",
                "right_source_hash",
                "condition",
                "source_span",
                "relation",
                "compiler_version",
            )
            payload["compiler_version"] = COMPILER_VERSION
            if (
                previous
                and previous.get("reviewer")
                and all(previous.get(field) == payload.get(field) for field in stable)
            ):
                for field in (
                    "review_state",
                    "qualification",
                    "valid_from",
                    "reviewer",
                    "reviewed_at",
                    "review_note",
                    "left_evidence_span",
                    "right_evidence_span",
                ):
                    payload[field] = previous.get(field)
            current.append(payload)
        self.relations = current

    def approved_relations(self) -> list[Relation]:
        return [
            relation_from_json(r)
            for r in self.relations
            if relation_from_json(r).review_state is ReviewState.approved
        ]

    def all_relations(self) -> list[Relation]:
        return [relation_from_json(r) for r in self.relations]

    def review_relation(
        self,
        left: str,
        right: str,
        issue_key: str,
        *,
        left_source_text: str,
        right_source_text: str,
        reviewer: str,
        rationale: str,
        qualification: Qualification,
        left_evidence_span: str,
        right_evidence_span: str,
        valid_from: dt.date | None = None,
    ) -> bool:
        from app.modules.address_lookup.rule_adapter.compiler import _source_contains, content_hash

        if not reviewer.strip() or not rationale.strip():
            raise InvalidCompilation("reviewer and rationale are required")
        for rel in self.relations:
            if (rel["left_rule_id"], rel["right_rule_id"], rel["issue_key"]) == (
                left,
                right,
                issue_key,
            ):
                if (
                    rel.get("compiler_version") != COMPILER_VERSION
                    or rel.get("left_source_hash") != content_hash(left_source_text)
                    or rel.get("right_source_hash") != content_hash(right_source_text)
                    or not _source_contains(left_source_text, left_evidence_span)
                    or not _source_contains(right_source_text, right_evidence_span)
                ):
                    raise InvalidCompilation("relation evidence is absent or stale")
                rel.update(
                    review_state=str(
                        ReviewState.approved
                        if qualification is not Qualification.unresolved
                        else ReviewState.needs_review
                    ),
                    qualification=str(qualification),
                    valid_from=valid_from.isoformat() if valid_from else None,
                    reviewer=reviewer,
                    reviewed_at=dt.datetime.now(dt.UTC).isoformat(),
                    review_note=rationale,
                    left_evidence_span=left_evidence_span,
                    right_evidence_span=right_evidence_span,
                )
                return True
        return False

    # -- queue ----------------------------------------------------------------
    def queue(self) -> list[dict[str, Any]]:
        """What a reviewer should look at, worst first.

        A rule with untranslated coverage text is the urgent case: every
        address it could reach is answering `unknown` because of it.
        """
        items: list[dict[str, Any]] = []
        for rule_id, payload in sorted(self.revisions.items()):
            unmapped = payload.get("unmapped_text", [])
            date_pending = bool(
                payload.get("effective_date_unresolved")
            ) and not self.date_review_for(
                rule_id, payload.get("rule_version_hash", ""), payload.get("source_hash")
            )
            if (
                payload.get("review_state")
                in (str(ReviewState.machine_verified), str(ReviewState.human_approved))
                and not unmapped
                and not date_pending
            ):
                continue
            items.append(
                {
                    "team_rule_id": rule_id,
                    "rule_version_hash": payload.get("rule_version_hash"),
                    "jurisdiction": payload.get("jurisdiction"),
                    "issue_key": payload.get("issue_key"),
                    "review_state": payload.get("review_state"),
                    "coverage_basis": payload.get("coverage_basis", "unresolved"),
                    "source_hash": payload.get("source_hash"),
                    "legacy_review_state": payload.get("legacy_review_state"),
                    "legacy_coverage_empty": payload.get("legacy_coverage_empty", False),
                    "legacy_priority": payload.get("legacy_priority", 2),
                    "coverage": payload.get("coverage"),
                    "exemptions": payload.get("exemptions"),
                    "unmapped_count": len(unmapped),
                    "effective_date_unresolved": date_pending,
                    "key_value_period": payload.get("key_value_period"),
                    "unmapped_text": unmapped,
                }
            )
        for rel in self.relations:
            if relation_from_json(rel).review_state is not ReviewState.approved:
                items.append({"relation": rel})
        items.sort(
            key=lambda i: (
                i.get("legacy_priority", 2),
                -i.get("unmapped_count", 0),
                str(i.get("team_rule_id", "zz")),
            )
        )
        return items
