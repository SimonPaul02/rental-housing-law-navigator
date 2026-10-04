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
    COMPILER_VERSION,
    Atom,
    CompiledRule,
    CoverageBasis,
    EffectiveDate,
    Expr,
    Field,
    InvalidCompilation,
    Op,
    Origin,
    Relation,
    RelationType,
    ReviewState,
    SourceAnchor,
    UnmappedClause,
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
        effective_dates=tuple(
            EffectiveDate(
                d["raw"],
                dt.date.fromisoformat(d["earliest"]),
                dt.date.fromisoformat(d["latest"]),
                d["precision"],
            )
            for d in payload.get("effective_dates", [])
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
        issue_key=payload.get("issue_key", ""),
        notes=tuple(payload.get("notes", [])),
    )


def relation_from_json(payload: dict) -> Relation:
    return Relation(
        left_rule_id=payload["left_rule_id"],
        right_rule_id=payload["right_rule_id"],
        issue_key=payload["issue_key"],
        relation=RelationType(payload["relation"]),
        anchor=_anchor_from(payload),
        condition=payload.get("condition"),
        review_state=ReviewState(payload.get("review_state", "needs_review")),
    )


# -------------------------------------------------------------------- store ---
@dataclass
class ReviewStore:
    """Compiled revisions, relations, and the queue of what needs a human."""

    path: Path
    revisions: dict[str, dict] = field(default_factory=dict)
    relations: list[dict] = field(default_factory=list)
    reviews: dict[str, dict] = field(default_factory=dict)

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
        )

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {
                    "revisions": self.revisions,
                    "relations": self.relations,
                    "reviews": self.reviews,
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
        }

    def apply_reviews(self, compiled: CompiledRule) -> CompiledRule:
        """Promote a revision a reviewer approved for exactly this text."""
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
        known = {(r["left_rule_id"], r["right_rule_id"], r["issue_key"]) for r in self.relations}
        for rel in relations:
            key = (rel.left_rule_id, rel.right_rule_id, rel.issue_key)
            if key not in known:
                self.relations.append(rel.to_json())
                known.add(key)

    def approved_relations(self) -> list[Relation]:
        return [
            relation_from_json(r)
            for r in self.relations
            if r.get("review_state") == str(ReviewState.approved)
        ]

    def all_relations(self) -> list[Relation]:
        return [relation_from_json(r) for r in self.relations]

    def approve_relation(self, left: str, right: str, issue_key: str, note: str = "") -> bool:
        for rel in self.relations:
            if (rel["left_rule_id"], rel["right_rule_id"], rel["issue_key"]) == (
                left,
                right,
                issue_key,
            ):
                rel["review_state"] = str(ReviewState.approved)
                rel["review_note"] = note
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
            if (
                payload.get("review_state")
                in (str(ReviewState.machine_verified), str(ReviewState.human_approved))
                and not unmapped
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
                    "unmapped_text": unmapped,
                }
            )
        for rel in self.relations:
            if rel.get("review_state") != str(ReviewState.approved):
                items.append({"relation": rel})
        items.sort(
            key=lambda i: (
                i.get("legacy_priority", 2),
                -i.get("unmapped_count", 0),
                str(i.get("team_rule_id", "zz")),
            )
        )
        return items
