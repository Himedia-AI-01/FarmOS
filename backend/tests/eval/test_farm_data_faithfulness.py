"""Farm-data faithfulness — given a mocked tool response, the agent's answer
must contain the exact numeric values from the mock (no rounding, no swap, no
projection). Catches "수치 변형 금지" violations.

Mock pattern: monkey-patch the relevant tool to return a fixed string, run the
farm-data subagent on the question, scan the answer for required numbers.
"""

from __future__ import annotations

import pytest

from tests.eval.conftest import load_jsonl

pytestmark = [pytest.mark.eval, pytest.mark.integration]


async def _run_farm_data_with_mock(query: str, mock_tool: str, mock_response: str) -> str:
    """Build a fresh LangChain Tool that returns mock_response and pass it
    explicitly to create_react_agent (bypassing module-level FARM_DATA_TOOLS).

    Earlier attempt monkey-patched the imported tool's `.coroutine` attribute,
    but LangChain Tool objects don't always re-resolve through the patched ref
    — agents bound to the original object kept calling the real implementation.
    Building a fresh tool with the same name makes the mock unambiguously the
    agent's target. Other farm-data tools are still passed through so the agent
    can compose multi-tool answers.
    """
    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_core.tools import StructuredTool
    from langgraph.prebuilt import create_react_agent

    from app.services.farm_agent import tools as t
    from app.services.farm_agent.models import build_llm_for
    from app.services.farm_agent.prompts import FARM_DATA_PROMPT

    real_tool = getattr(t, mock_tool, None)
    if real_tool is None:
        return ""

    async def _mock_coroutine(**kwargs):  # noqa: ARG001
        return mock_response

    # Try to copy the real tool's input schema so the agent can still call it
    # with the same arguments. Fall back to a permissive no-arg tool.
    try:
        mock_struct_tool = StructuredTool.from_function(
            coroutine=_mock_coroutine,
            name=real_tool.name,
            description=real_tool.description,
            args_schema=getattr(real_tool, "args_schema", None),
        )
    except Exception:
        mock_struct_tool = StructuredTool.from_function(
            coroutine=_mock_coroutine,
            name=real_tool.name,
            description=real_tool.description,
        )

    # Build the tools list with the mock substituted in.
    tools_list = []
    for tool in t.FARM_DATA_TOOLS:
        if tool.name == real_tool.name:
            tools_list.append(mock_struct_tool)
        else:
            tools_list.append(tool)

    llm = build_llm_for("farm_data", max_tokens=1024)
    agent = create_react_agent(
        llm,
        tools=tools_list,
        prompt=SystemMessage(content=FARM_DATA_PROMPT),
    )
    state = await agent.ainvoke({"messages": [HumanMessage(content=query)]})
    msgs = state.get("messages", [])
    return getattr(msgs[-1], "content", "") if msgs else ""


@pytest.mark.parametrize("case", load_jsonl("farm_data_faithfulness"))
async def test_faithfulness(case, record_result):
    answer = await _run_farm_data_with_mock(
        case["q"], case["mock_tool"], case["mock_response"],
    )

    failures = []
    # Number passthrough — accept multiple formats: "62255", "62,255", "62255원".
    # We treat "expected_numbers_in_answer" as an OR list: the case has a list
    # of acceptable representations of the SAME number, and at least one must
    # appear. (Earlier strict ALL-of-list match caused false failures when the
    # agent picked the comma form vs the bare form.)
    expected_numbers = case.get("expected_numbers_in_answer", [])
    if expected_numbers and not any(n in answer for n in expected_numbers):
        failures.append(f"none of {expected_numbers} present")

    for kw in case.get("expected_keywords", []):
        if kw not in answer:
            failures.append(f"missing keyword {kw!r}")

    passed = not failures
    record_result(
        surface="farm_data_faithfulness",
        case_id=case["q"][:40],
        passed=passed,
        detail="; ".join(failures) if failures else "OK",
        query=case["q"],
        answer=answer,
    )
    if not passed:
        pytest.fail(f"{case['q']}: {failures}\n--- answer ---\n{answer[:400]}")
