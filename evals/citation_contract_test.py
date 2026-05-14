"""End-to-end eval harness for the citation contract."""

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

    assert out["status"] in ("ok", "refused_verifier_exhausted"), (
        f"{case['id']}: unexpected status {out['status']}"
    )

    if out["status"] == "ok":
        violations = find_violations(out["text"], frozenset())
        if violations:
            assert out["footnotes"], (
                f"{case['id']}: numerals present without footnotes: {violations}"
            )
    else:
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

    assert out["status"] in ("ok", "refused_verifier_exhausted"), (
        f"{case['id']}: unexpected status {out['status']}"
    )

    if out["status"] == "ok":
        violations = find_violations(out["text"], frozenset())
        if case.get("must_be_refused") and violations:
            assert out["footnotes"], (
                f"{case['id']}: literal numeral '{violations[0]['numeral']}' "
                f"in 'ok' answer without any footnote"
            )
    else:
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
