"""Subsidy calibration — runs the subsidy subagent on labeled questions and
checks (a) verdict alignment with ground truth, (b) citation presence.

Uses the actual production subsidy subagent (with Sonnet→Opus auto-escalation),
so this test is sensitive to prompt drift and SUBSIDY_PROMPT calibration changes.

Cost: ~30 questions × ~$0.08 Sonnet (90% of cases) + ~3 escalations × $0.27 Opus
= ~$3 per full run. Mark as `eval` so it runs only on demand.
"""

from __future__ import annotations

import re
import pytest

from tests.eval.conftest import load_jsonl

pytestmark = [pytest.mark.eval, pytest.mark.integration]

# Citation forms the agent actually produces, observed across eval runs:
#   [doc > 제N조] / [doc > VI. 영농기록]                       (canonical)
#   [doc > CHAPTER 1 > III. 사업추진 절차]                     (chapter-prefixed)
#   [II-3 / II-6]                                              (clause id)
#   시행지침 제N조 / 제N조 제N항                                (inline)
#   (출처: 농림부 청년농업인 영농정착지원사업 운영지침)         (parenthetical for
#                                                              non-시행지침 sources)
_DOC_CITATION_RE = re.compile(
    r"\[(?:doc|시행지침|CHAPTER\s*\d+|II|III|IV|V|VI|VII)[^\]]*\]"
    r"|시행지침\s*제\s*\d+\s*조"
    r"|제\s*\d+\s*조\s*제?\s*\d*\s*항"
    r"|\(출처:\s*[^)]+\)"
)

# Verdict detection — UNCLEAR > MANDATORY > OPTIONAL > UNKNOWN (priority order).
# UNCLEAR markers must be unambiguous (per SUBSIDY_PROMPT calibration rules).
# MANDATORY markers: "✅ 필수", explicit "의무", "감액", "준수.*하셔야".
# OPTIONAL markers: any informational answer that doesn't trigger MANDATORY/UNCLEAR
# AND mentions specifics (amount/eligibility/procedure) — broadest match last.
# UNCLEAR markers — broadened from real-eval phrasings. The agent often says
# "별도 조항은 찾지 못했습니다" / "의무화한 조항은 찾지 못했습니다" / "본문에 직접
# 등장하지 않습니다" rather than the exact "명확한 의무 조항" phrasing.
# All variants below are honest UNCLEAR signals (≠ everyday "확인 권장" advice).
_UNCLEAR_PATTERNS = [
    # "별도/의무화한/명확한 조항을 찾지 못" — common phrasings
    r"(?:별도|의무화한|명확한)\s*(?:의무\s*)?조항(?:을|은|이)?\s*찾지\s*못",
    r"조항(?:은)?\s*시행지침(?:에서)?\s*직접\s*찾(?:을\s*수\s*없|지\s*못)",
    r"본문에\s*직접\s*등장하지\s*않",
    r"별도\s*분류.*?명시되어?\s*있지\s*않",
    r"별도\s*명시가?\s*없",
    r"별도\s*항목(?:으로)?(?:는)?\s*명시(?:하지|되어\s*있지)?\s*않",
    r"정확한\s*조항\s*못\s*찾",
    r"UNCLEAR",
    r"확인하지\s*못했습니다",
    r"검색\s*결과에?\s*나타나지\s*않",
    r"시행지침에서?\s*확인되지\s*않",
]
_MANDATORY_PATTERNS = [
    r"✅\s*필수", r"필수입니다", r"의무입니다", r"의무이며", r"의무\s*사항",
    r"감액", r"준수.*?하셔야", r"반드시\s*[가-힣]", r"반드시 .{0,15}야",
]
_OPTIONAL_PATTERNS = [
    r"❌\s*의무는 아닙", r"임의", r"자율",
    # Amount/schedule informational. Comma+space variant required because real
    # answers say "2,000만 원" (KRW formatting) not "2000만원" (no comma).
    r"\d{1,3}(?:,\d{3})*\s*만\s*원",   # "2,000만 원" or "2000만원"
    r"\d{2,3}만원",                    # legacy short form
    r"\d+\s*ha", r"\d+㎡", r"\d+\s*평",
    # Procedural / eligibility markers (mined from real eval answers,
    # without targeting specific failing cases):
    r"신청\s*기간", r"신청\s*기한", r"신청\s*절차",
    r"자격\s*요건", r"등록\s*절차", r"\d+년\s*이상\s*거주", r"가산금",
    r"단가는?", r"지급\s*시기", r"발급",
    r"\d+\s*일\s*이내", r"매년\s*\d", r"원칙적으로",
    r"주관(?:합니다|하며)",
    # Verb forms common in informational answers:
    r"확인합니다", r"열람", r"제출\s*합니다?", r"검토",
    r"행정정보로 확인", r"주민등록(?:초본|등본)",
    r"이의신청", r"행정처분",
]


async def _run_subsidy(query: str) -> str:
    """Run question through the subsidy escalation graph; return final text."""
    from langchain_core.messages import HumanMessage

    from app.services.farm_agent.subsidy_escalation import build_subsidy_agent_with_escalation
    from app.services.farm_agent.tools import SUBSIDY_TOOLS

    graph = build_subsidy_agent_with_escalation(SUBSIDY_TOOLS)
    state = await graph.ainvoke({"messages": [HumanMessage(content=query)]})
    msgs = state.get("messages", [])
    return getattr(msgs[-1], "content", "") if msgs else ""


def _detect_verdict(answer: str) -> str:
    """Priority: UNCLEAR > MANDATORY > OPTIONAL > UNKNOWN.

    Refined leading-⚠ rule: ⚠ alone is not a UNCLEAR marker — agent uses ⚠ for
    general emphasis ("⚠️ 두 배가 아니라 두 배 이상"). Only treat as UNCLEAR
    when the leading section ALSO contains a "couldn't find / not in 시행지침"
    phrase. This avoids over-correcting MANDATORY answers that just happen to
    use ⚠ for emphasis.
    """
    head = (answer or "")[:200]
    leading_warning = "⚠" in head
    head_uncertainty = any(
        re.search(p, head) for p in (
            r"찾(지|을)\s*(못|없)",
            r"확인되지\s*않",
            r"명시되어\s*있지\s*않",
            r"별도\s*조항",
            r"명시.*?없습?니다",
            r"등장하지\s*않",
        )
    )
    if leading_warning and head_uncertainty:
        return "UNCLEAR"

    for p in _UNCLEAR_PATTERNS:
        if re.search(p, answer):
            return "UNCLEAR"
    for p in _MANDATORY_PATTERNS:
        if re.search(p, answer):
            return "MANDATORY"
    for p in _OPTIONAL_PATTERNS:
        if re.search(p, answer):
            return "OPTIONAL"
    return "UNKNOWN"


@pytest.mark.parametrize("case", load_jsonl("subsidy_calibration"))
async def test_subsidy_calibration(case, record_result):
    q = case["q"]
    expected_verdict = case["expected_verdict"]
    expected_cites = case.get("expected_citations_min", 0)

    answer = await _run_subsidy(q)

    detected_verdict = _detect_verdict(answer)
    citations = _DOC_CITATION_RE.findall(answer)

    failures = []
    if detected_verdict != expected_verdict:
        failures.append(f"verdict expected={expected_verdict} got={detected_verdict}")
    if len(citations) < expected_cites:
        failures.append(f"citations expected≥{expected_cites} got={len(citations)}")

    passed = not failures
    record_result(
        surface="subsidy_calibration",
        case_id=q[:40],
        passed=passed,
        detail="; ".join(failures) if failures else f"verdict={detected_verdict} cites={len(citations)}",
        query=q,
        answer=answer,
    )
    # Soft assertion — we want all failures visible, not stop-on-first-fail
    if not passed:
        pytest.fail(f"{q}: {failures}\n--- answer ---\n{answer[:600]}")
