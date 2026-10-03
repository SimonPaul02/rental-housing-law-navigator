"""Model selection and per-model request shaping.

Current Claude models differ in which knobs they accept, and sending the
wrong one is a 400 rather than a silent no-op:

  * `temperature` is removed across the board - determinism comes from the
    extraction cache, not the sampler;
  * `thinking: {type: "adaptive"}` and `output_config.effort` work on Opus 5
    and Sonnet 5 but error on Haiku 4.5, which still takes a thinking budget;
  * Haiku 4.5 has a 200K context against 1M on the others - ample for this
    corpus, whose largest document is ~40K tokens, but worth asserting.

So the request body is built per model rather than assumed.
"""

from __future__ import annotations

from dataclasses import dataclass


# Prices are USD per million tokens, Anthropic first-party rates.
# The Batch API bills at 50% of these.
@dataclass(frozen=True, slots=True)
class ModelSpec:
    model_id: str
    input_per_mtok: float
    output_per_mtok: float
    batch_input_per_mtok: float
    batch_output_per_mtok: float
    cache_read_per_mtok: float
    context_tokens: int
    supports_adaptive_thinking: bool
    supports_effort: bool
    note: str


CATALOGUE: dict[str, ModelSpec] = {
    "claude-opus-5-5": ModelSpec(
        "claude-opus-5-5",
        4.00,
        20.00,
        2.00,
        10.00,
        0.20,
        1_000_000,
        True,
        True,
        "Newest Opus and cheaper than Opus 5. Best statutory judgement; "
        "cache reads are 0.05x base, half the usual multiplier.",
    ),
    "claude-sonnet-5-5": ModelSpec(
        "claude-sonnet-5-5",
        2.00,
        10.00,
        1.00,
        5.00,
        0.20,
        1_000_000,
        True,
        True,
        "Newest Sonnet. Recommended default: half Opus 5.5's price.",
    ),
    "claude-opus-5": ModelSpec(
        "claude-opus-5",
        5.00,
        25.00,
        2.50,
        12.50,
        0.50,
        1_000_000,
        True,
        True,
        "Superseded by Opus 5.5, which is both newer and cheaper.",
    ),
    "claude-sonnet-5": ModelSpec(
        "claude-sonnet-5",
        2.00,
        10.00,
        1.00,
        5.00,
        0.20,
        1_000_000,
        True,
        True,
        "Same price as Sonnet 5.5; prefer 5.5.",
    ),
    "claude-haiku-4-5": ModelSpec(
        "claude-haiku-4-5",
        1.00,
        5.00,
        0.50,
        2.50,
        0.10,
        200_000,
        False,
        False,
        "Cheapest. No adaptive thinking or effort; may miss statutory nuance.",
    ),
}

BATCH_DISCOUNT = 0.5


def spec_for(model_id: str) -> ModelSpec:
    if model_id not in CATALOGUE:
        raise ValueError(
            f"Unknown model {model_id!r}. Known: {sorted(CATALOGUE)}. "
            "Add it to CATALOGUE with its pricing before using it."
        )
    return CATALOGUE[model_id]


def estimate_cost(
    model_id: str,
    input_tokens: int,
    output_tokens: int,
    *,
    batch: bool = False,
    cached_input_tokens: int = 0,
) -> float:
    """Batch rates are taken from the published table rather than derived by
    halving - the two agree today, but the table is what is billed."""
    spec = spec_for(model_id)
    in_rate = spec.batch_input_per_mtok if batch else spec.input_per_mtok
    out_rate = spec.batch_output_per_mtok if batch else spec.output_per_mtok
    cache_rate = spec.cache_read_per_mtok * (BATCH_DISCOUNT if batch else 1.0)
    return round(
        input_tokens / 1_000_000 * in_rate
        + output_tokens / 1_000_000 * out_rate
        + cached_input_tokens / 1_000_000 * cache_rate,
        4,
    )


def build_params(
    model_id: str,
    *,
    system: list[dict],
    messages: list[dict],
    max_tokens: int,
    json_schema: dict,
) -> dict:
    """Request body valid for this specific model.

    Structured outputs go through `output_config.format` rather than the
    `messages.parse()` helper, because the Batch API takes raw params.
    """
    spec = spec_for(model_id)
    params: dict = {
        "model": spec.model_id,
        "max_tokens": max_tokens,
        "system": system,
        "messages": messages,
        "output_config": {"format": {"type": "json_schema", "schema": json_schema}},
    }
    if spec.supports_adaptive_thinking:
        # Opus 5 runs adaptive by default, but Sonnet 5 needs it asked for.
        params["thinking"] = {"type": "adaptive"}
    return params
