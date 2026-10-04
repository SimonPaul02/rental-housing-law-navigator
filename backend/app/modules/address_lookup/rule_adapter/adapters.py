"""IO for the rule adapter: the database, the store, and the model.

The compiler and the evaluator are pure. This is the only module in the pair
that touches a session, a file or the Anthropic API, which is what keeps the
decision logic replayable from frozen inputs.
"""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.modules.address_lookup.rule_adapter import compiler
from app.modules.address_lookup.rule_adapter.models import (
    BOOLEAN_FIELDS,
    Field,
    Op,
    Relation,
    ReviewState,
    rule_version_hash,
)
from app.modules.address_lookup.rule_adapter.review import ReviewStore, relation_from_json

log = logging.getLogger(__name__)

#: Where compiled revisions and review decisions live for the demo.
STORE_PATH = settings.data_root / "data" / "compiled_rules.json"

PROMPT_VERSION = "3"


def store() -> ReviewStore:
    return ReviewStore.load(STORE_PATH)


def compile_all(
    records: list[Any],
    *,
    review_store: ReviewStore | None = None,
    proposals: dict[str, dict] | None = None,
    sources: dict[str, str] | None = None,
    persist: bool = True,
):
    """Compile every rule once, reusing anything already stored for this text.

    Returns (compiled rules by id, relations, newly compiled ids).
    """
    store_ = review_store if review_store is not None else store()
    known_ids = {getattr(r, "team_rule_id", "") for r in records}
    compiled: dict[str, Any] = {}
    relations: list[Relation] = []
    fresh: list[str] = []

    for record in records:
        rule_id = getattr(record, "team_rule_id", "")
        version = rule_version_hash(record)

        source_text = (sources or {}).get(getattr(record, "source_doc_id", None))
        source_hash = compiler.content_hash(source_text)
        existing = store_.get(rule_id, version, source_hash)
        if existing is not None:
            rels, _ = compiler.rule_relations(record, list(existing.classified), known_ids, records)
            compiled[rule_id] = store_.apply_reviews(existing)
            relations.extend(rels)
            continue

        rule, rels = compiler.compile_rule(
            record,
            known_rule_ids=known_ids,
            proposal=(proposals or {}).get(rule_id),
            peers=records,
            source_text=source_text,
        )
        compiled[rule_id] = store_.apply_reviews(rule)
        relations.extend(rels)
        fresh.append(rule_id)
        store_.put(rule)

    current_relations: list[Relation] = []
    for rel in relations:
        left, right = compiled[rel.left_rule_id], compiled[rel.right_rule_id]
        anchor = replace(rel.anchor, source_hash=left.source_hash) if rel.anchor else None
        current_relations.append(
            replace(
                rel,
                anchor=anchor,
                left_version_hash=left.rule_version_hash,
                right_version_hash=right.rule_version_hash,
                left_source_hash=left.source_hash,
                right_source_hash=right.source_hash,
            )
        )
    store_.put_relations(current_relations)
    if persist:
        store_.save()
        from app.modules.address_lookup import service

        service.clear_compiled_cache()

    # Relations are evaluated from the store, so an approval sticks.
    return compiled, store_.all_relations(), fresh


# ------------------------------------------------------------ model proposals ---
PROPOSAL_SYSTEM = """\
You translate one clause of United States residential rental housing law into a \
small, fixed condition format. You are given clauses that a deterministic parser \
could not represent, together with the rule they come from.

For each clause, give one of two verdicts.

`condition` - the clause narrows WHICH BUILDINGS OR TENANCIES the rule covers \
by something testable: a unit count, a construction or certificate date, owner \
occupancy, a seasonal or subsidised status. Translate it into atoms.

`no_building_condition` - the clause only names the regulated actor or repeats \
the jurisdiction. This verdict is a review suggestion and never grants coverage.

Omit a clause entirely rather than guess. An omitted clause is reported as \
unknown, which is a correct answer; a wrong verdict silently changes who the law \
covers. In particular, do NOT use `no_building_condition` for a clause that \
plainly does narrow coverage but whose threshold you cannot pin down - say \
nothing about it instead.

Allowed fields and the values they take:
  legal_city (string), legal_state (two-letter string), use_code (string)
  units (integer), year_built (integer), owner_unit_count (integer)
  certificate_of_occupancy_date (full date, YYYY-MM-DD)
  owner_occupied, owner_is_natural_person, seasonal_rental,
  building_is_subsidised, los_angeles_rso_membership,
  san_francisco_rent_ordinance_membership (no value - use op is_true or is_false)

Allowed operators: eq, ne, lt, lte, gt, gte on valued fields; is_true, is_false \
on boolean fields.

Rules:
- `source_span` must be copied VERBATIM from the source_document, and must \
support that specific clause. A phrase appearing only in the rule record is \
not source evidence.
- Return the clause_id supplied with each clause.
- Never invent a threshold, a date or a field. If the clause says "older \
buildings" with no year, omit it.
- A certificate-of-occupancy cutoff needs a full date. A bare year is not one.
- Several conditions that must hold together belong in one entry's `atoms` list.
- Do not restate the jurisdiction: geography is handled separately.
- A clause that depends on another program ("units subject to the RSO") \
is a material condition. Omit it if you cannot express membership; do not clear it.
"""


def _proposal_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["clauses"],
        "properties": {
            "clauses": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["clause_id", "text", "verdict", "source_span", "atoms"],
                    "properties": {
                        "text": {"type": "string"},
                        "clause_id": {"type": "string"},
                        "verdict": {"enum": ["condition", "no_building_condition"]},
                        "source_span": {"type": "string"},
                        "atoms": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["id", "field", "op", "source_span"],
                                "properties": {
                                    "id": {"type": "string"},
                                    "field": {"enum": [str(f) for f in Field]},
                                    "op": {"enum": [str(o) for o in Op]},
                                    "value": {"type": ["string", "integer", "null"]},
                                    "source_span": {"type": "string"},
                                },
                            },
                        },
                    },
                },
            }
        },
    }


def proposal_cache_path() -> Path:
    return settings.data_root / "data" / "rule_proposals.json"


def load_proposals() -> dict[str, dict]:
    path = proposal_cache_path()
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload.get("proposals", {})


def save_proposals(proposals: dict[str, dict]) -> None:
    path = proposal_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"prompt_version": PROMPT_VERSION, "proposals": proposals}, indent=2, sort_keys=True
        )
        + "\n",
        encoding="utf-8",
    )


def proposal_key(record: Any, source_text: str | None = None) -> str:
    from app.modules.address_lookup.rule_adapter.compiler import content_hash

    return "|".join(
        [
            record.team_rule_id,
            rule_version_hash(record),
            content_hash(source_text) or "missing-source",
            settings.extraction_model,
            PROMPT_VERSION,
        ]
    )


def cached_suggestion(
    proposals: dict[str, dict], record: Any, source_text: str | None
) -> dict | None:
    """Old model output is only a suggestion; the current compiler revalidates it."""
    current = proposals.get(proposal_key(record, source_text))
    if current is not None:
        return current
    return next(
        (
            value
            for key, value in proposals.items()
            if key.startswith(f"{record.team_rule_id}|") and isinstance(value, dict)
        ),
        None,
    )


async def propose_for(
    record: Any, unmapped_texts: list[str], source_text: str | None = None
) -> dict:
    """Ask the model to translate the clauses the parser could not.

    Cached by rule version, model and prompt version by the caller, so a
    re-run costs nothing. The proposal is a suggestion about wording we already
    hold; every atom it returns is validated and span-checked before it is
    trusted.
    """
    from app.core.llm import get_client
    from app.modules.rule_extraction.pipeline import models as model_specs

    client = get_client()
    spec = model_specs.spec_for(settings.extraction_model)

    clause_list = "\n".join(f"- {text}" for text in unmapped_texts)
    body = (
        f'<rule id="{getattr(record, "team_rule_id", "")}" '
        f'jurisdiction="{getattr(record, "jurisdiction", "")}" '
        f'level="{getattr(record, "level", "")}">\n'
        "<coverage_conditions>"
        f"{getattr(record, 'coverage_conditions', '') or ''}"
        "</coverage_conditions>\n"
        f"<exemptions>{getattr(record, 'exemptions', '') or ''}</exemptions>\n"
        f"<requirement>{getattr(record, 'requirement', '') or ''}</requirement>\n"
        f"<quoted_span>{getattr(record, 'quoted_span', '') or ''}</quoted_span>\n"
        f"<source_document>{(source_text or '')[:30000]}</source_document>\n"
        f"</rule>\n\nClauses to translate:\n{clause_list}\n"
    )

    extra: dict = {}
    if spec.supports_adaptive_thinking:
        extra["thinking"] = {"type": "adaptive"}

    response = await client.messages.create(
        model=spec.model_id,
        max_tokens=4000,
        **extra,
        system=[
            {
                "type": "text",
                "text": PROPOSAL_SYSTEM,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": body}],
        output_config={"format": {"type": "json_schema", "schema": _proposal_schema()}},
    )

    text = "".join(block.text for block in response.content if getattr(block, "type", "") == "text")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        log.warning("unparseable proposal for %s", getattr(record, "team_rule_id", "?"))
        return {"clauses": []}


def boolean_field_names() -> set[str]:
    return {str(f) for f in BOOLEAN_FIELDS}


def unreviewed_summary(review_store: ReviewStore) -> dict[str, int]:
    revisions = list(review_store.revisions.values())
    return {
        "rules": len(review_store.revisions),
        "needs_review": sum(
            1
            for r in revisions
            if r.get("review_state")
            not in (
                str(ReviewState.machine_verified),
                str(ReviewState.human_approved),
                str(ReviewState.machine_classified),
            )
        ),
        # Answered at low confidence on the machine's own reading, and still
        # queued for a reviewer.
        "machine_classified": sum(
            r.get("review_state") == str(ReviewState.machine_classified) for r in revisions
        ),
        "machine_verified": sum(
            r.get("review_state") == str(ReviewState.machine_verified) for r in revisions
        ),
        "human_approved": sum(
            r.get("review_state") == str(ReviewState.human_approved) for r in revisions
        ),
        "pending_clauses": sum(len(r.get("unmapped_text", [])) for r in revisions),
        "with_unmapped_text": sum(1 for r in revisions if r.get("unmapped_text")),
        "relations": len(review_store.relations),
        "date_reviews_needed": sum(
            bool(r.get("effective_date_unresolved"))
            and not review_store.date_review_for(
                r.get("team_rule_id", ""), r.get("rule_version_hash", ""), r.get("source_hash")
            )
            for r in revisions
        ),
        "relations_approved": sum(
            relation_from_json(r).review_state is ReviewState.approved
            for r in review_store.relations
        ),
    }
