"""Routing eval — deterministic, fast.

Tests that `_detect_single_domain` (the fast-path classifier) routes questions
correctly. Doesn't call any LLM — pure keyword logic. Catches regressions in
the routing keyword tables when SUBSIDY/DIAGNOSIS prompts change.

Maps eval values to detector outputs:
  expected_route="direct_tool"          → detector returns None (fast_path handles)
  expected_route="diagnosis-agent"      → detector returns "diagnosis-agent"
  expected_route="subsidy-agent"        → detector returns "subsidy-agent"
  expected_route="farm-data-agent"      → detector returns "farm-data-agent"
  expected_route="orchestrator"         → detector returns None (multi-domain or meta)

The "direct_tool" vs "orchestrator" distinction is informational — both yield
None from the detector but represent different orchestrator behavior. We treat
None-result questions as a pass for either label since the detector deliberately
abdicates to the orchestrator in both cases.
"""

from __future__ import annotations

import pytest

from app.api.farm_agent import _detect_single_domain
from tests.eval.conftest import load_jsonl


@pytest.mark.parametrize("case", load_jsonl("routing"))
def test_routing_classification(case, record_result):
    q = case["q"]
    expected = case["expected_route"]
    actual = _detect_single_domain(q)

    if expected in ("direct_tool", "orchestrator"):
        # Detector should abdicate (return None) — orchestrator decides.
        passed = actual is None
        detail = f"q={q!r} expected={expected} detector_returned={actual}"
    else:
        passed = actual == expected
        detail = f"q={q!r} expected={expected} got={actual}"

    record_result(
        surface="routing",
        case_id=q[:30],
        passed=passed,
        detail=detail,
        query=q,
    )
    assert passed, detail
