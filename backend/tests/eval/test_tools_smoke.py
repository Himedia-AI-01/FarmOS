"""Tools smoke test — calls each LangChain tool with a basic input and checks
the response shape. Hits real DB / external services (KMA, KAMIS, ChromaDB) so
runs slower than unit tests; mark as `integration`.

Pass criteria are intentionally loose (key existence, substring presence) so
schema drift in upstream APIs surfaces as a single failed case rather than
breaking the whole suite.
"""

from __future__ import annotations

import json
import pytest

from tests.eval.conftest import load_jsonl

pytestmark = [pytest.mark.eval, pytest.mark.integration]


_TOOL_REGISTRY = {}


def _registry():
    """Lazy-build a name → callable map from tools.py (avoids import-time DB)."""
    global _TOOL_REGISTRY
    if _TOOL_REGISTRY:
        return _TOOL_REGISTRY
    from app.services.farm_agent import tools as t

    for name in dir(t):
        obj = getattr(t, name)
        if hasattr(obj, "ainvoke") or hasattr(obj, "invoke"):
            _TOOL_REGISTRY[name] = obj
    return _TOOL_REGISTRY


@pytest.mark.parametrize("case", load_jsonl("tools_smoke"))
async def test_tool_smoke(case, record_result):
    name = case["tool"]
    args = case.get("args", {})
    tool = _registry().get(name)
    if tool is None:
        pytest.skip(f"tool {name} not found in registry")

    try:
        # LangChain tools take dict input via ainvoke.
        result = await tool.ainvoke(args)
        result_text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
    except Exception as exc:
        if case.get("allow_empty"):
            record_result(surface="tools", case_id=name, passed=True, detail=f"allowed empty/error: {exc}")
            return
        record_result(surface="tools", case_id=name, passed=False, detail=f"raised: {exc}")
        pytest.fail(f"{name} raised: {exc}")

    if case.get("allow_empty") and not result_text:
        record_result(surface="tools", case_id=name, passed=True, detail="empty allowed")
        return

    passed = True
    failures = []

    for key in case.get("expect_keys", []):
        if key not in result_text:
            passed = False
            failures.append(f"missing key {key!r}")

    for substr in case.get("expect_substrings", []):
        if substr not in result_text:
            passed = False
            failures.append(f"missing substring {substr!r}")

    record_result(
        surface="tools",
        case_id=name,
        passed=passed,
        detail="; ".join(failures) if failures else f"OK (len={len(result_text)})",
        query=f"{name}({json.dumps(args, ensure_ascii=False)})",
        answer=result_text if not passed else "",
    )
    assert passed, f"{name}: {failures}\n--- response head ---\n{result_text[:300]}"
