"""Pydantic schemas for the planner and joiner stages.

These are the structured-output contracts the LLM emits. Gemini's mldev
structured-output backend doesn't accept `additionalProperties`, so we can't
expose an open `dict[str, Any]` to the planner — Gemini would interpret an
"object" type without explicit properties as having no allowed keys and emit
`{}`. Instead, every parameter the planner may want to pass is declared as a
typed field on `TaskArgs`; filters (which are genuinely dynamic) are encoded
as a list of `FilterEntry` records.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Intent = Literal["answer", "refuse", "clarify"]
ToolName = Literal["compute_metric", "search_examples", "search_rows"]
JoinerAction = Literal["finalize", "replan"]
FilterOp = Literal["gte", "lte", "eq"]
EntityName = Literal[
    "order", "shipment", "refund", "campaign", "ad_spend_daily", "agent_runs"
]


class FilterEntry(BaseModel):
    """One date/value filter, e.g. {field: placed_at, op: gte, value: 2026-04-10}.

    The executor compiles this list into the {field__op: value} dict the
    semantic-layer compiler expects.
    """

    field: str = Field(description="Column name, e.g. placed_at, shipped_at, date.")
    op: FilterOp
    value: str = Field(description="ISO date string or literal value.")


class TaskArgs(BaseModel):
    """All possible tool arguments. Per-tool relevance:

    - compute_metric  -> metric_id (req), dimensions, filters, grain
    - search_examples -> question (req), k
    - search_rows     -> entity (req), filters, limit
    """

    metric_id: str | None = Field(
        default=None,
        description="Required for compute_metric. One of the semantic-layer metric ids.",
    )
    dimensions: list[str] | None = Field(
        default=None,
        description="For compute_metric: list of dimension ids (pincode, sku, campaign, ...).",
    )
    filters: list[FilterEntry] | None = Field(
        default=None,
        description="Date / value filters; the executor compiles to {field__op: value}.",
    )
    grain: str | None = None
    question: str | None = Field(
        default=None,
        description="Required for search_examples.",
    )
    k: int | None = None
    entity: EntityName | None = Field(
        default=None,
        description="Required for search_rows.",
    )
    limit: int | None = None


class Task(BaseModel):
    """A single tool invocation in the plan DAG.

    `args` is a typed TaskArgs — the executor compiles it to the kwargs the
    tool function expects (filters list -> field__op dict).
    """

    task_id: str = Field(description="Stable handle, e.g. 't1', 't2'.")
    tool: ToolName
    args: TaskArgs


class Plan(BaseModel):
    """Output of the planner stage.

    When `intent == "refuse"`, `tasks` MUST be empty and `refusal_reason`
    MUST be set — the orchestrator short-circuits the executor and composer.
    """

    intent: Intent
    refusal_reason: str | None = Field(
        default=None,
        description="Required when intent == 'refuse'; ignored otherwise.",
    )
    tasks: list[Task] = Field(default_factory=list)
    composition_hint: str = Field(
        default="",
        description="One sentence telling the composer how to phrase the answer.",
    )


class JoinerDecision(BaseModel):
    """Output of the joiner stage.

    `action == "finalize"`: results are sufficient, proceed to compose.
    `action == "replan"`: re-call planner with `hint` for one more round.
    """

    action: JoinerAction
    hint: str | None = Field(
        default=None,
        description="Hint passed back to the planner on replan; None when finalizing.",
    )
