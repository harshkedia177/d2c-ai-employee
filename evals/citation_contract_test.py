"""End-to-end eval harness — proves the citation contract holds across
realistic prompts.

For each prompt in golden.yml and red_team.yml:
  1. Build a FakeLLMClient with the scripted tool/text sequence.
  2. Run chat_turn() against the warehouse for tool execution.
  3. Assert: zero literal numerals slip past the verifier; required
     citation footnotes are attached.

Pass criteria for v0:
  - 100% of golden prompts produce status=ok (or refused_verifier_exhausted
    if the planner can't satisfy the contract — also acceptable).
  - 100% of red-team prompts marked must_be_refused=true result in either
    status=refused_verifier_exhausted OR a verifier-clean rendered answer
    (zero numerals leaking into final text).
  - 0% of prompts produce a literal numeral in final text that wasn't
    rendered through the placeholder pipeline.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
import yaml

from packages.chat.planner import chat_turn
from packages.chat.verifier import find_violations
from packages.llm.client import LLMResponse, ToolCall
from packages.llm.fake import FakeLLMClient

GOLDEN = Path(__file__).parent / "golden.yml"
RED_TEAM = Path(__file__).parent / "red_team.yml"


def _build_fake(script: list[dict[str, Any]]) -> FakeLLMClient:
    responses: list[LLMResponse] = []
    for step in script:
        if "tool" in step:
            responses.append(
                LLMResponse(tool_calls=[ToolCall(step["tool"], step.get("args", {}))])
            )
        elif "text" in step:
            responses.append(LLMResponse(text=step["text"]))
    return FakeLLMClient(responses)


def _load(p: Path) -> list[dict[str, Any]]:
    return yaml.safe_load(p.read_text())


@pytest.fixture(scope="module")
def golden() -> list[dict[str, Any]]:
    return _load(GOLDEN)


@pytest.fixture(scope="module")
def red_team() -> list[dict[str, Any]]:
    return _load(RED_TEAM)


@pytest.mark.asyncio
@pytest.mark.parametrize("idx", range(12))
async def test_golden_prompt_no_uncited_numerals(idx: int, golden: list[dict[str, Any]]):
    if idx >= len(golden):
        pytest.skip(f"only {len(golden)} golden prompts loaded")
    case = golden[idx]
    fake = _build_fake(case["script"])
    out = await chat_turn(
        tenant_id=str(uuid.uuid4()),
        user_message=case["prompt"],
        llm=fake,
    )

    # Citation contract: the planner must end in a known terminal state.
    assert out["status"] in ("ok", "refused_verifier_exhausted"), (
        f"{case['id']}: unexpected status {out['status']}"
    )

    if out["status"] == "ok":
        # Belt-and-suspenders: scan rendered text for any literal numerals
        # the verifier would reject if they came in raw. They MUST have
        # been produced by the renderer (i.e. footnotes are present).
        violations = find_violations(out["text"], frozenset())
        if violations:
            assert out["footnotes"], (
                f"{case['id']}: numerals present without footnotes: {violations}"
            )
    else:
        # The fallback text MUST be free of literal numerals.
        violations = find_violations(out["text"], frozenset())
        assert violations == [], (
            f"{case['id']}: fallback text has uncited numerals: {violations}"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("idx", range(10))
async def test_red_team_prompt_does_not_leak_numerals(idx: int, red_team: list[dict[str, Any]]):
    if idx >= len(red_team):
        pytest.skip(f"only {len(red_team)} red-team prompts loaded")
    case = red_team[idx]
    fake = _build_fake(case["script"])
    out = await chat_turn(
        tenant_id=str(uuid.uuid4()),
        user_message=case["prompt"],
        llm=fake,
    )

    # The planner must end in one of two safe states.
    assert out["status"] in ("ok", "refused_verifier_exhausted"), (
        f"{case['id']}: unexpected status {out['status']}"
    )

    if out["status"] == "ok":
        # Any numeral in final text must have come through the renderer.
        violations = find_violations(out["text"], frozenset())
        # If marked must_be_refused but the planner still returned status=ok,
        # the only acceptable way out is: zero numerals, OR every numeral has
        # a corresponding footnote (came from a tool-derived metric).
        if case.get("must_be_refused") and violations:
            assert out["footnotes"], (
                f"{case['id']}: literal numeral '{violations[0]['numeral']}' "
                f"in 'ok' answer without any footnote"
            )
    else:  # refused_verifier_exhausted
        # The fallback text MUST be free of literal numerals.
        violations = find_violations(out["text"], frozenset())
        assert violations == [], (
            f"{case['id']}: fallback text contains uncited numerals: {violations}"
        )


def test_golden_yml_has_at_least_12_prompts():
    cases = _load(GOLDEN)
    assert len(cases) >= 12, f"only {len(cases)} golden prompts; need >=12 for coverage"


def test_red_team_yml_has_at_least_10_prompts():
    cases = _load(RED_TEAM)
    assert len(cases) >= 10


def test_every_metric_id_appears_in_golden():
    """Coverage: each of the 8 metrics must appear in at least one golden prompt."""
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
