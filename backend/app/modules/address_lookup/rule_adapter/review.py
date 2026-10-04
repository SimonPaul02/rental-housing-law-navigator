"""Where compiled revisions and review decisions live.

Kept separate from Module A's raw rule on purpose: re-extraction rewrites the
rule table, and an approved interpretation of legal text is work that must not
be erased by it. Approval is bound to a `rule_version_hash`, so correcting the
wording of a rule invalidates the approval rather than silently carrying it
over to text a reviewer never saw.

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
    Atom,
    CompiledRule,
    EffectiveDate,
    Expr,
    Field,
    Op,
    Origin,
    Relation,
    RelationType,
    ReviewState,
    SourceAnchor,
    UnmappedClause,
)


# ------------------------------------------------------------ deserialising ---
def _anchor_from(payload: dict) -> SourceAnchor | None:
    span = payload.get("source_span")
    if not span:
        return None
    return SourceAnchor(span, payload.get("source_doc_id"), payload.get("source_url"))


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
    kind = "all" if "all" in payload else "any"
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
            UnmappedClause(u["text"], Origin(u["origin"]), u["reason"])
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
        self.revisions[compiled.team_rule_id] = compiled.to_json()

    def get(self, team_rule_id: str, rule_version_hash: str) -> CompiledRule | None:
        """The stored revision, but only if it describes the current text."""
        payload = self.revisions.get(team_rule_id)
        if not payload or payload.get("rule_version_hash") != rule_version_hash:
            return None
        return compiled_from_json(payload)

    def review_for(self, team_rule_id: str, rule_version_hash: str) -> dict | None:
        decision = self.reviews.get(team_rule_id)
        if not decision or decision.get("rule_version_hash") != rule_version_hash:
            return None
        return decision

    def approve(
        self, team_rule_id: str, rule_version_hash: str, note: str, by: str = "review"
    ) -> None:
        """Record that a human read the clause and the translation together."""
        self.reviews[team_rule_id] = {
            "rule_version_hash": rule_version_hash,
            "review_state": str(ReviewState.approved),
            "note": note,
            "by": by,
            "at": dt.datetime.now(dt.UTC).isoformat(),
        }

    def apply_reviews(self, compiled: CompiledRule) -> CompiledRule:
        """Promote a revision a reviewer approved for exactly this text."""
        decision = self.review_for(compiled.team_rule_id, compiled.rule_version_hash)
        if decision and decision.get("review_state") == str(ReviewState.approved):
            compiled.review_state = ReviewState.approved
            compiled.notes = (*compiled.notes, f"approved in review: {decision.get('note', '')}")
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
            if payload.get("review_state") == str(ReviewState.approved) and not unmapped:
                continue
            items.append(
                {
                    "team_rule_id": rule_id,
                    "rule_version_hash": payload.get("rule_version_hash"),
                    "jurisdiction": payload.get("jurisdiction"),
                    "issue_key": payload.get("issue_key"),
                    "review_state": payload.get("review_state"),
                    "unmapped_count": len(unmapped),
                    "unmapped_text": unmapped,
                }
            )
        for rel in self.relations:
            if rel.get("review_state") != str(ReviewState.approved):
                items.append({"relation": rel})
        items.sort(key=lambda i: (-i.get("unmapped_count", 0), str(i.get("team_rule_id", "zz"))))
        return items
