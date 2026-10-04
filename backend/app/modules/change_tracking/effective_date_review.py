"""Source-checked AB 325 effective-date correction for extracted D022 rules."""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Document, Rule
from app.modules.address_lookup.service import clear_compiled_cache
from app.modules.rule_extraction.pipeline import audit


def ab325_date_evidence(text: str) -> tuple[str, str] | None:
    compact = re.sub(r"\s+", " ", text)
    match = re.search(
        r"AB[- ]325\s*\(AGUIAR[- ]CURRY\)\s*,?\s*CH\.?\s*338"
        r".{0,160}?EFFECTIVE DATE\s*:\s*JANUARY\s+1\s*,?\s*2026",
        compact,
        flags=re.I,
    )
    return ("2026-01-01", match.group(0)) if match else None


async def apply_ab325_date(session: AsyncSession) -> list[str]:
    evidence_doc = await session.get(Document, "D092")
    if not evidence_doc or not evidence_doc.body or not evidence_doc.content_hash:
        raise ValueError("D092 official California courts summary has not been captured")
    evidence = ab325_date_evidence(evidence_doc.body)
    if evidence is None:
        raise ValueError("D092 does not contain an AB 325, Chapter 338 effective-date span")
    date, span = evidence
    rules = list(
        (
            await session.execute(
                select(Rule).where(
                    Rule.source_doc_id == "D022",
                    Rule.category == "algorithmic_rent_setting",
                    Rule.citation.ilike("%16729%"),
                )
            )
        ).scalars()
    )
    if not rules:
        raise ValueError("No verified D022 Section 16729 rules exist to correct")
    updated: list[str] = []
    for rule in rules:
        if rule.effective_date not in (None, date):
            raise ValueError(
                f"{rule.team_rule_id} already has a conflicting date {rule.effective_date}"
            )
        if rule.effective_date == date:
            continue
        rule.effective_date = date
        updated.append(rule.team_rule_id)
        await audit.record(
            session,
            None,
            audit.Channel.extraction_log,
            {
                "stage": "reviewed_effective_date",
                "team_rule_id": rule.team_rule_id,
                "source_doc_id": "D092",
                "source_url": evidence_doc.url,
                "source_hash": evidence_doc.content_hash,
                "quoted_span": span,
                "effective_date": date,
            },
        )
    await session.flush()
    _record_date_reviews(rules, await session.get(Document, "D022"), evidence_doc.body, span)
    clear_compiled_cache()
    return updated


def _record_date_reviews(
    rules: list[Rule], bill: Document | None, evidence: str, span: str
) -> None:
    """Bind the date to each rule's compilation, so it is source-backed there too.

    Setting `Rule.effective_date` alone is not enough: the compiler checks a
    date against the rule's own source, and D022 - the bill text - never
    states it. The review quotes D092 instead, names the act in both, and is
    bound to the rule's current version and D022's current body.
    """
    from app.modules.address_lookup.rule_adapter import adapters
    from app.modules.address_lookup.rule_adapter.compiler import compile_rule

    if bill is None or not bill.body:
        raise ValueError("D022 has no stored body to bind the date review to")
    store = adapters.store()
    for rule in rules:
        compiled, _ = compile_rule(rule, source_text=bill.body)
        store.review_dates(
            compiled,
            source_text=bill.body,
            reviewer="source-check:ab325_date_evidence",
            rationale=(
                "The California courts' 2025 legislative summary (D092) states the effective "
                "date of AB 325, Chapter 338; the bill text (D022) names the same act."
            ),
            effective_dates=[{"raw": "2026-01-01", "source_span": span}],
            evidence_doc_id="D092",
            evidence_text=evidence,
            act_markers=("AB 325", "338"),
        )
    store.save()
