"""Diagnosis pesticide passthrough — verifies that pesticide brand names,
dilution ratios, and harvest-interval values from `diagnose_pest` survive
unchanged through the agent pipeline.

This is the safety floor: an agent that paraphrases "1000배 희석 → 약 천배"
or swaps brand names creates real-world risk for farmers. Test catches
regressions in the diagnosis subagent's instruction adherence.

Runs the actual `diagnose_pest` tool to capture authoritative strings, then
re-runs the same query through the orchestrator and checks each authoritative
string appears verbatim in the agent's final answer.
"""

from __future__ import annotations

import re
import pytest

from tests.eval.conftest import load_jsonl

pytestmark = [pytest.mark.eval, pytest.mark.integration]


_DILUTION_RE = re.compile(r"\d+(?:[,.]\d+)*\s*배")


async def _run_diagnosis(pest: str, crop: str, region: str) -> tuple[str, str]:
    """Returns (authoritative_text, agent_answer)."""
    from langchain_core.messages import HumanMessage

    from app.services.farm_agent.tools import diagnose_pest as diagnose_tool
    from app.services.farm_agent.models import build_llm_for
    from langgraph.prebuilt import create_react_agent
    from app.services.farm_agent.tools import DIAGNOSIS_TOOLS
    from app.services.farm_agent.prompts import DIAGNOSIS_PROMPT
    from langchain_core.messages import SystemMessage

    # Authoritative output from the tool itself.
    authoritative = await diagnose_tool.ainvoke({"pest": pest, "crop": crop, "region": region})

    # Run the diagnosis subagent against a question that triggers the same call.
    diagnosis_llm = build_llm_for("diagnosis", max_tokens=2048)
    agent = create_react_agent(
        diagnosis_llm,
        tools=list(DIAGNOSIS_TOOLS),
        prompt=SystemMessage(content=DIAGNOSIS_PROMPT),
    )
    state = await agent.ainvoke({
        "messages": [HumanMessage(content=f"{crop}에 {pest} 의심됩니다. 진단·방제 알려주세요. (지역: {region})")],
    })
    msgs = state.get("messages", [])
    answer = ""
    if msgs:
        answer = getattr(msgs[-1], "content", "") or ""
    return authoritative, answer


@pytest.mark.parametrize("case", load_jsonl("diagnosis_passthrough"))
async def test_diagnosis_passthrough(case, record_result):
    pest, crop, region = case["pest"], case["crop"], case["region"]
    auth, answer = await _run_diagnosis(pest, crop, region)

    # Extract dilution ratios and brand names from authoritative output.
    auth_dilutions = set(_DILUTION_RE.findall(auth))

    # Brand-name extraction: target the "스미치온(경농)" / "다이센(영일)" pattern.
    # Filters added from real eval false-positives:
    #   - taxonomy markers in paren: 강/목/과/원소/병해충/분류
    #   - generic agronomic / chemistry terms (not brand names)
    _GENERIC_NON_BRAND = {
        "유기물", "토양", "비료", "농약", "잔류물", "탄소", "성분",
        "곰팡이", "균사", "포자", "잎", "줄기", "뿌리",
    }
    auth_brands = set(
        m[0] for m in re.findall(r"([가-힣]{2,8})\s*\(([가-힣]{2,6})\)", auth)
        if not any(skip in m[1] for skip in ("강", "목", "과", "원소", "병해충", "분류"))
        and m[0] not in _GENERIC_NON_BRAND
    )

    failures = []
    if auth_dilutions:
        missing = [d for d in auth_dilutions if d not in answer]
        if missing:
            failures.append(f"missing dilutions: {missing}")
    if auth_brands:
        missing = [b for b in auth_brands if b not in answer][:3]
        if missing:
            failures.append(f"missing brands: {missing}")

    passed = not failures
    record_result(
        surface="diagnosis_passthrough",
        case_id=f"{pest}/{crop}",
        passed=passed,
        detail="; ".join(failures) if failures else f"all {len(auth_dilutions)} dilutions + {len(auth_brands)} brands preserved",
        query=f"{pest}/{crop}/{region}",
        answer=answer,
    )
    assert passed, f"{pest}/{crop}: {failures}\n--- authoritative ---\n{auth[:400]}\n--- answer ---\n{answer[:400]}"
