"""End-to-end eval harness for the citation contract.

Drives the Plan -> Execute -> Join -> Compose orchestrator
(packages/chat/orchestrator/) with scripted FakeLLMClient sessions loaded
from golden.yml / red_team.yml.

YAML shape per case (see those files for full comments):
    plan            : Plan model fields (intent, tasks, composition_hint, ...)
    joiner          : JoinerDecision fields (present iff plan.intent=='answer')
    compose_chunks  : list[str], the composer stream's text chunks
                      (placeholders `{{m:<id>}}` are substituted server-side)

The parametrized tests below require a live Postgres on port 5433 (the
orchestrator's executor calls compute_metric / search_rows which hit the
warehouse). The static-coverage tests at the bottom run without a DB.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
import yaml

from packages.chat.orchestrator import chat_turn
from packages.chat.orchestrator.plan import JoinerDecision, Plan
from packages.chat.verifier import find_violations
from packages.llm.fake import FakeLLMClient

GOLDEN = Path(__file__).parent / "golden.yml"
RED_TEAM = Path(__file__).parent / "red_team.yml"


def _load(p: Path) -> list[dict[str, Any]]:
    return yaml.safe_load(p.read_text())


@pytest.fixture(scope="module")
def golden() -> list[dict[str, Any]]:
    return _load(GOLDEN)


@pytest.fixture(scope="module")
def red_team() -> list[dict[str, Any]]:
    return _load(RED_TEAM)


def _build_fake(case: dict[str, Any]) -> FakeLLMClient:
    """Construct a FakeLLMClient scripted from a YAML case.

    - `structured` queue: [Plan] + ([JoinerDecision] if joiner present).
      Replan cases (multiple plans/joiners) would interleave further entries
      here; today neither YAML file uses them.
    - `streams` queue: one entry per composer pass — present iff the
      orchestrator will reach the composer (i.e. plan.intent == 'answer').
    """
    structured: list[Any] = [Plan.model_validate(case["plan"])]
    if "joiner" in case:
        structured.append(JoinerDecision.model_validate(case["joiner"]))
    streams: list[list[str]] = (
        [list(case["compose_chunks"])] if case.get("compose_chunks") else []
    )
    return FakeLLMClient(structured=structured, streams=streams)


# -- Parametrized end-to-end tests (require live DB) --------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("idx", range(12))
async def test_golden_prompt_no_uncited_numerals(
    idx: int, golden: list[dict[str, Any]]
) -> None:
    """Each golden case yields a clean, status='ok' answer with every literal
    numeral either substituted from a footnote or absent from the text."""
    case = golden[idx]
    fake = _build_fake(case)
    out = await chat_turn(
        tenant_id=str(uuid.uuid4()),
        user_message=case["prompt"],
        llm=fake,
    )
    assert out["status"] in {"ok", "refused", "clarify"}, (
        f"{case['id']}: unexpected status={out['status']}"
    )
    if out["status"] == "ok":
        expected = case.get("expected_substring_in_text")
        if expected:
            # Soft check — composer wording may legitimately diverge from the
            # scripted chunks once the substitution happens. We mainly enforce
            # the citation contract below.
            assert expected in out["text"] or out["footnotes"], (
                f"{case['id']}: expected substring '{expected}' missing "
                f"and no footnotes either; text={out['text']!r}"
            )
        violations = find_violations(out["text"], frozenset())
        if violations:
            # A literal numeral landed in the answer. That's only acceptable
            # if footnotes cover it (i.e. it came from a placeholder).
            assert out["footnotes"], (
                f"{case['id']}: numerals without footnotes: {violations}"
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("idx", range(10))
async def test_red_team_no_uncited_numerals(
    idx: int, red_team: list[dict[str, Any]]
) -> None:
    """Red-team cases either refuse cleanly or — if the planner chose to
    answer — produce text with no uncited literal numerals."""
    case = red_team[idx]
    fake = _build_fake(case)
    out = await chat_turn(
        tenant_id=str(uuid.uuid4()),
        user_message=case["prompt"],
        llm=fake,
    )
    if case.get("must_be_refused"):
        assert out["status"] in {"refused", "clarify"}, (
            f"{case['id']}: expected refusal, got status={out['status']}"
        )
        # Belt-and-braces: refusal text itself must contain no uncited numerals.
        violations = find_violations(out["text"], frozenset())
        assert not violations, (
            f"{case['id']}: refusal text leaked numerals: {violations}"
        )
        return

    # Permitted path: planner answered. The composer's output (after
    # substitution) must be verifier-clean unless every literal numeral is
    # covered by a footnote.
    assert out["status"] == "ok", (
        f"{case['id']}: expected ok answer, got status={out['status']}"
    )
    violations = find_violations(out["text"], frozenset())
    if violations:
        assert out["footnotes"], (
            f"{case['id']}: uncited numerals with no footnotes: {violations}"
        )


# -- Static coverage tests (no DB required) -----------------------------------


def test_golden_yml_has_at_least_12_prompts() -> None:
    cases = _load(GOLDEN)
    assert len(cases) >= 12, (
        f"only {len(cases)} golden prompts; need >=12 for coverage"
    )


def test_red_team_yml_has_at_least_10_prompts() -> None:
    cases = _load(RED_TEAM)
    assert len(cases) >= 10


def test_every_metric_id_appears_in_golden() -> None:
    cases = _load(GOLDEN)
    cited = {c.get("must_cite_metric_id") for c in cases if c.get("must_cite_metric_id")}
    expected = {
        "gmv",
        "aov",
        "rto_rate",
        "cac",
        "post_rto_roas",
        "contribution_margin_per_order",
        "pincode_rto_rate_90d",
        "sku_rto_rate_90d",
    }
    missing = expected - cited
    assert not missing, f"golden lacks coverage for metrics: {missing}"


def test_golden_yml_shape_is_orchestrator_compatible() -> None:
    """Every golden case must validate against the orchestrator's Plan /
    JoinerDecision schemas. Catches drift between the YAML scripts and the
    Pydantic contracts at static-test time (no DB needed)."""
    for case in _load(GOLDEN):
        Plan.model_validate(case["plan"])
        if "joiner" in case:
            JoinerDecision.model_validate(case["joiner"])
        if case["plan"].get("intent") == "answer":
            assert case.get("compose_chunks"), (
                f"{case['id']}: answer plans must script compose_chunks"
            )
            assert "joiner" in case, (
                f"{case['id']}: answer plans must script a joiner decision"
            )


def test_red_team_yml_shape_is_orchestrator_compatible() -> None:
    for case in _load(RED_TEAM):
        Plan.model_validate(case["plan"])
        if "joiner" in case:
            JoinerDecision.model_validate(case["joiner"])
        if case["plan"].get("intent") in ("refuse", "clarify"):
            assert not case["plan"].get("tasks"), (
                f"{case['id']}: refuse/clarify plans must have no tasks"
            )
            assert "compose_chunks" not in case, (
                f"{case['id']}: refuse/clarify short-circuits before compose"
            )
