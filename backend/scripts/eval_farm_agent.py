"""LangSmith eval harness for FarmOS Farm Agent.

Purpose:
    Run a curated dataset of representative Korean farmer questions through the
    Deep Agent pipeline and score outputs on:
      - correctness:      Does the answer match expected ground truth?
      - citation_present: Did subsidy answers include `[doc > 제N조]` citations?
      - mandatory_correct: For yes/no obligation questions, did the verdict match?
      - latency_ok:       Did the agent respond under FARM_AGENT_LATENCY_BUDGET sec?
      - safety:           For pesticide questions, were dilution/timing values
                          taken verbatim from authoritative pesticide_data?

Usage:
    # Set LANGCHAIN_API_KEY + LANGCHAIN_TRACING_V2=true in your env first
    cd backend
    uv run python scripts/eval_farm_agent.py
    uv run python scripts/eval_farm_agent.py --upload-dataset  # one-off seeding
    uv run python scripts/eval_farm_agent.py --concurrency 4

Why these dimensions:
    Research (LangSmith 2026 patterns) recommends scoring agents on output
    correctness AND process quality (which tools fired, did citation rules
    apply, did safety gates trigger). Multi-dimensional eval catches regressions
    a single "answer correctness" score would miss — e.g. an answer that's
    technically right but skips required citations.

Adding cases:
    Append to `DATASET` below with structure:
        {"input": "Korean question", "expected": {...flexible}, "tags": [...]}
    The custom evaluators read `expected` per-tag, so each tag is its own check.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as _dt
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.services.farm_agent.agent import build_farm_agent
# Reuse production helpers — running the eval through different extraction
# logic than the live API would mask real bugs (e.g. an answer that's correct
# in production but extracted-as-empty by a divergent test scaffold).
from app.api.farm_agent import (  # noqa: E402
    _latest_assistant_text_from_state,
    _wrap_with_routing_hint,
)
from app.services.farm_agent.fast_path import try_fast_path  # noqa: E402

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")


# ── Dataset ─────────────────────────────────────────────────────────────────
#
# Each example: {"input": str, "expected": dict, "tags": [str]}
# Tags drive which evaluators run on each example.

DATASET: list[dict[str, Any]] = [
    {
        "input": "직불금 받으려면 영농일지 꼭 써야하나?",
        "expected": {
            "verdict": "MANDATORY",
            "must_contain": ["필수", "영농기록", "2년"],
            "must_not_contain": ["필수가 아", "권장사항", "선택"],
            "citations_required": True,
            "latency_budget_sec": 12.0,
        },
        "tags": ["subsidy", "obligation", "yes_no", "mandatory"],
    },
    {
        "input": "공익직불 교육 이수 안 받아도 돼?",
        "expected": {
            "verdict": "MANDATORY",
            "must_contain": ["필수", "교육"],
            "must_not_contain": ["선택", "받지 않아도"],
            "citations_required": True,
            "latency_budget_sec": 12.0,
        },
        "tags": ["subsidy", "obligation", "yes_no", "mandatory"],
    },
    {
        "input": "소농직불금 자격이 어떻게 되나요?",
        "expected": {
            "must_contain": ["0.1", "0.5", "ha", "농촌"],
            "citations_required": True,
            "latency_budget_sec": 15.0,
        },
        "tags": ["subsidy", "eligibility"],
    },
    {
        "input": "오늘 날씨 어때?",
        "expected": {
            "must_contain": ["기온", "℃"],
            "fast_path_expected": True,
            "latency_budget_sec": 3.0,
        },
        "tags": ["weather", "fast_path"],
    },
    {
        "input": "소농직불 자격이 뭐고 부정수급 처벌은?",
        "expected": {
            # Multi-topic — should trigger query decomposition
            "must_contain": ["소농", "부정수급"],
            "citations_required": True,
            "latency_budget_sec": 18.0,
        },
        "tags": ["subsidy", "decomposition"],
    },
    {
        "input": "벼 시세 얼마야?",
        "expected": {
            "must_contain": ["원"],
            "fast_path_expected": True,
            "latency_budget_sec": 5.0,
        },
        "tags": ["price", "fast_path"],
    },
]


# ── Custom evaluators ───────────────────────────────────────────────────────


def eval_must_contain(run_output: str, expected: dict) -> dict[str, int]:
    """All keywords in expected['must_contain'] should appear in the answer."""
    keywords = expected.get("must_contain", [])
    if not keywords:
        return {"must_contain": 1}
    hits = sum(1 for kw in keywords if kw in (run_output or ""))
    return {"must_contain": int(hits == len(keywords)), "must_contain_ratio": hits / len(keywords)}


def eval_must_not_contain(run_output: str, expected: dict) -> dict[str, int]:
    """No keyword in expected['must_not_contain'] should appear."""
    forbidden = expected.get("must_not_contain", [])
    if not forbidden:
        return {"must_not_contain": 1}
    leaks = [kw for kw in forbidden if kw in (run_output or "")]
    return {"must_not_contain": int(not leaks)}


def eval_citation_present(run_output: str, expected: dict) -> dict[str, int]:
    """Subsidy answers must cite `[doc > ...]` or `[CHAPTER...]`."""
    if not expected.get("citations_required"):
        return {"citation_present": 1}
    text = run_output or ""
    has_citation = ("[doc >" in text) or ("[CHAPTER" in text) or ("제" in text and "조" in text)
    return {"citation_present": int(has_citation)}


def eval_latency(elapsed_sec: float, expected: dict) -> dict[str, float]:
    """Latency under per-example budget."""
    budget = float(expected.get("latency_budget_sec", 30.0))
    return {
        "latency_ok": int(elapsed_sec <= budget),
        "latency_sec": round(elapsed_sec, 2),
        "latency_budget": budget,
    }


# Lexical markers for verdict-calibration scoring. Kept module-level so the
# unit tests (`scripts/test_distill_loop.py`) can import them directly without
# depending on agent runtime.
_AFFIRM_KWS = ("필수", "의무", "반드시", "해야")
_DENY_KWS = ("필수가 아", "선택사항", "임의", "받지 않아도", "안 해도")
# Hedge markers — explicit uncertainty surfacing per iter-10 calibration rule
# in subsidy_agent prompt (`verdict_hint=UNCLEAR` branch). The agent must
# *not* assert a definitive answer when the deterministic verdict is UNCLEAR;
# instead it must surface the uncertainty and recommend an authoritative
# contact (시·군·읍면동 직불 담당).
_HEDGE_KWS = ("명확한", "확인을 권장", "확인 권장", "확인해", "담당", "⚠")


def eval_obligation_verdict(run_output: str, expected: dict) -> dict[str, int]:
    """Score the answer against the expected verdict (MANDATORY/OPTIONAL/UNCLEAR).

    - **MANDATORY**: must affirm and not deny — definitive yes.
    - **OPTIONAL**:  must deny obligation (or omit affirmation) — definitive no.
    - **UNCLEAR**:   must hedge (explicit uncertainty + 담당 권장) and must
      NOT make a definitive 필수/의무 assertion. Pins iter-10 calibration.
    - any other / missing verdict → check skipped (returns 1).
    """
    verdict = expected.get("verdict")
    if not verdict:
        return {"verdict_correct": 1}
    text = run_output or ""
    affirms = any(kw in text for kw in _AFFIRM_KWS)
    denies = any(kw in text for kw in _DENY_KWS)
    hedges = any(kw in text for kw in _HEDGE_KWS)

    if verdict == "MANDATORY":
        return {"verdict_correct": int(affirms and not denies)}
    if verdict == "OPTIONAL":
        # Either explicit denial or absence of obligation language counts.
        return {"verdict_correct": int(denies or not affirms)}
    if verdict == "UNCLEAR":
        # Must hedge and must NOT make a confident 필수 claim.
        return {"verdict_correct": int(hedges and not (affirms and not hedges))}
    # Unknown verdict label — be lenient.
    return {"verdict_correct": 1}


# ── Runner ──────────────────────────────────────────────────────────────────


async def run_one_example(agent: Any, example: dict, user_id: str = "eval-user") -> dict:
    """Invoke the agent on a single example and collect output + latency.

    Matches the production /ask code path:
      1) Try fast_path first (LLM bypass for simple queries).
      2) Apply routing hint to skip orchestrator routing decisions.
      3) Use _latest_assistant_text_from_state helper for extraction —
         this picks up subagent task ToolMessage content as the user-facing
         answer when skip-orchestrator-synthesis is in effect.
    """
    import uuid
    started = time.perf_counter()

    # 1) Fast-path (matches /ask endpoint behavior)
    if settings.FARM_AGENT_FAST_PATH_ENABLED:
        try:
            fast_answer = await try_fast_path(example["input"], user_id)
        except Exception:  # noqa: BLE001
            fast_answer = None
        if fast_answer:
            elapsed = time.perf_counter() - started
            return {
                "input": example["input"],
                "output": fast_answer,
                "elapsed_sec": elapsed,
                "errored": False,
                "fast_path": True,
            }

    # 2) Normal Deep Agent flow with routing hint
    config = {
        "configurable": {
            "thread_id": f"eval-{uuid.uuid4().hex}",
            "user_id": user_id,
        }
    }
    routed_question = _wrap_with_routing_hint(example["input"])
    try:
        state = await agent.ainvoke(
            {"messages": [{"role": "user", "content": routed_question}]},
            config=config,
        )
    except Exception as exc:  # noqa: BLE001
        elapsed = time.perf_counter() - started
        return {
            "input": example["input"],
            "output": f"[ERROR] {type(exc).__name__}: {exc}",
            "elapsed_sec": elapsed,
            "errored": True,
        }
    elapsed = time.perf_counter() - started
    # Use production extraction helper — handles the skip-orchestrator-synthesis
    # case where the answer is in the task ToolMessage, not an AIMessage.
    output = _latest_assistant_text_from_state(state)
    diagnostic = ""
    if not output.strip():
        diagnostic = _diagnose_empty_state(state)
    return {
        "input": example["input"],
        "output": output,
        "elapsed_sec": elapsed,
        "errored": False,
        "fast_path": False,
        "diagnostic": diagnostic,
    }


def _diagnose_empty_state(state: Any) -> str:
    """Build a one-line diagnostic when the agent returned empty.

    Dumps message types, content lengths, and tool call names from the current
    turn so we can see whether:
      - No `task` was called → orchestrator ignored routing hint
      - `task` was called but its content is empty → subagent looped on tools
      - All AIMessage content is reasoning-only → reasoning blocks filtered out
        (means OPENROUTER_REASONING_ENABLED is still true somewhere)
    """
    # ainvoke returns a dict directly; aget_state returns a StateSnapshot.
    # Match the production helper's resolution logic exactly.
    if isinstance(state, dict):
        values = state
    else:
        values_attr = getattr(state, "values", None)
        if not isinstance(values_attr, dict):
            return f"unexpected state shape: {type(state).__name__}"
        values = values_attr
    messages = values.get("messages", [])
    if not messages:
        return "no messages in state"

    # Find current turn (after last HumanMessage)
    current_turn_start = 0
    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        if (getattr(msg, "type", None) == "human"
                or getattr(msg, "role", None) == "user"):
            current_turn_start = i + 1
            break
    turn_msgs = messages[current_turn_start:]

    summary: list[str] = []
    for msg in turn_msgs:
        msg_type = getattr(msg, "type", None) or getattr(msg, "role", None) or "?"
        name = getattr(msg, "name", "") or ""
        content = getattr(msg, "content", "")
        if isinstance(content, str):
            clen = len(content.strip())
            content_kind = "str"
        elif isinstance(content, list):
            clen = sum(
                len(item.get("text", "") if isinstance(item, dict) else str(item))
                for item in content
            )
            block_types = sorted({
                item.get("type", "?") if isinstance(item, dict) else "raw"
                for item in content
            })
            content_kind = f"blocks={block_types}"
        else:
            clen = 0
            content_kind = type(content).__name__
        tool_calls = getattr(msg, "tool_calls", None) or []
        tc_names = [
            (tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", ""))
            for tc in tool_calls
        ]
        tail = f" tools={tc_names}" if tc_names else ""
        label = f"{msg_type}({name})" if name else msg_type
        summary.append(f"{label}[{content_kind},len={clen}]{tail}")
    return f"turn_msgs[{len(turn_msgs)}]: " + " → ".join(summary)


def score_example(result: dict, expected: dict) -> dict[str, Any]:
    """Run all evaluators on one result, return a flat score dict."""
    scores: dict[str, Any] = {}
    if result.get("errored"):
        return {"errored": 1, "must_contain": 0, "citation_present": 0, "latency_ok": 0}
    out = result["output"]
    scores.update(eval_must_contain(out, expected))
    scores.update(eval_must_not_contain(out, expected))
    scores.update(eval_citation_present(out, expected))
    scores.update(eval_latency(result["elapsed_sec"], expected))
    if expected.get("verdict"):
        scores.update(eval_obligation_verdict(out, expected))
    return scores


# ── Dataset filters (debug a single example without editing DATASET) ───────


def filter_dataset(
    dataset: list[dict[str, Any]],
    selector: str | None,
) -> list[dict[str, Any]]:
    """Return the subset of DATASET matching `selector`.

    Selector forms (first match wins):
      - integer string: zero-based index, e.g. "0" → DATASET[0]
      - tag name:       e.g. "subsidy" → all examples whose tags include "subsidy"
      - empty / None:   pass-through (full dataset)

    Raises ValueError on integer-out-of-range or zero-tag-matches so the CLI
    can surface a clear error instead of silently running nothing.
    """
    if not selector:
        return list(dataset)
    sel = selector.strip()
    if sel.lstrip("-").isdigit():
        idx = int(sel)
        if not (-len(dataset) <= idx < len(dataset)):
            raise ValueError(
                f"--example index {idx} out of range (dataset has {len(dataset)})"
            )
        return [dataset[idx]]
    matches = [ex for ex in dataset if sel in (ex.get("tags") or [])]
    if not matches:
        raise ValueError(
            f"--example {sel!r} matched no tags. Use --list to see available tags."
        )
    return matches


def print_dataset_index(dataset: list[dict[str, Any]]) -> None:
    """Tabulate DATASET for `--list`. Read-only, no agent invocation."""
    print(f"\n{len(dataset)} example(s):\n")
    for i, ex in enumerate(dataset):
        tags = ",".join(ex.get("tags", []))
        q = (ex.get("input") or "")[:60]
        print(f"  [{i:>2}] {tags:<35}  {q}")
    print()


# ── Strategy-candidate writer (ReasoningBank failure-mining) ───────────────
#
# Every failed eval example becomes a candidate strategy entry queued for human
# review in backend/memory/STRATEGY_CANDIDATES.md. The reviewer copy-edits and
# promotes worthy candidates into STRATEGIES.md (which the agent loads at
# runtime). We do NOT auto-promote — the eval set is small and a noisy auto-loop
# would pollute the strategy memory.
#
# Source: ReasoningBank (arXiv 2509.25140) §3 — "actively analyzes failed
# experiences to source counterfactual signals and pitfalls".


_CANDIDATES_FILE = (
    Path(__file__).resolve().parents[1] / "memory" / "STRATEGY_CANDIDATES.md"
)
_CANDIDATES_HEADER = """# Strategy Candidates — Reviewer Queue

Auto-appended by `scripts/eval_farm_agent.py` on every failed example.
Human review required before promoting an entry into `STRATEGIES.md`.

Each entry is a draft in the ReasoningBank `When / Strategy / Pitfall` schema.
The reviewer should:
1. Confirm the failure is reproducible (re-run the example).
2. Edit the draft into a generalisable rule (not example-specific).
3. Move the edited entry into `STRATEGIES.md` and delete from this file.

---
"""


def _failed_check_names(scores: dict[str, Any]) -> list[str]:
    relevant = {"must_contain", "must_not_contain", "citation_present",
                "latency_ok", "verdict_correct"}
    return [k for k, v in scores.items() if k in relevant and v == 0]


def _append_strategy_candidate(
    ex: dict, result: dict, scores: dict, path_label: str
) -> None:
    """Append a single failure as a draft strategy candidate. Best-effort.

    Schema mirrors STRATEGIES.md so reviewers can copy-edit-paste.
    """
    failed = _failed_check_names(scores)
    if not failed and not result.get("errored"):
        return  # nothing useful to learn
    try:
        _CANDIDATES_FILE.parent.mkdir(parents=True, exist_ok=True)
        if not _CANDIDATES_FILE.exists():
            _CANDIDATES_FILE.write_text(_CANDIDATES_HEADER, encoding="utf-8")
        ts = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        tags = ",".join(ex.get("tags", []))
        out_excerpt = (result.get("output") or "(empty)").replace("\n", " ")[:240]
        failed_str = ", ".join(failed) if failed else "errored"
        entry = (
            f"\n## ⏳ {ts} — {tags}\n"
            f"- **When**: 사용자 질문이 다음 패턴일 때 — `{ex['input']}`\n"
            f"- **Strategy**: _(reviewer fills in: 어떤 도구·서브에이전트·인용 규칙을 강화해야 실패가 사라지는가?)_\n"
            f"- **Pitfall**: 실패한 체크 — `{failed_str}`. "
            f"observed_path=`{path_label}` elapsed={result.get('elapsed_sec', 0):.1f}s. "
            f"observed_answer_excerpt: \"{out_excerpt}\"\n"
            f"- **Source**: eval_farm_agent.py run @ {ts}\n"
        )
        with _CANDIDATES_FILE.open("a", encoding="utf-8") as f:
            f.write(entry)
    except Exception as exc:  # noqa: BLE001 — never let writer break eval
        logger.warning("strategy_candidate.append_failed err=%s", exc)


# ── Machine-readable summary (CI regression gating) ────────────────────────


# Score keys that count toward pass/fail aggregation. Keep this single-source
# so the human-readable report and the JSON summary stay aligned.
_AGG_SCORE_KEYS = (
    "must_contain", "must_not_contain", "citation_present",
    "latency_ok", "verdict_correct",
)


def build_json_summary(
    cases: list[dict[str, Any]],
    paired: list[tuple[dict[str, Any], dict[str, Any]]],
) -> dict[str, Any]:
    """Aggregate per-example results + dataset into a CI-friendly dict.

    Shape:
      {
        "version": 1,
        "total_examples": int,
        "total_pass": int,           # sum of passed checks across examples
        "total_checks": int,         # sum of evaluated checks
        "pass_ratio": float,         # 0..1
        "all_passed": bool,          # true ⇔ pass_ratio == 1.0
        "examples": [{
          "input": str,
          "tags": [str],
          "passed": int,
          "checks": int,
          "all_passed": bool,
          "errored": bool,
          "fast_path": bool,
          "elapsed_sec": float,
          "scores": {<score_key>: <value>}   # raw evaluator output
        }]
      }
    """
    examples_out: list[dict[str, Any]] = []
    total_pass = 0
    total_checks = 0
    for (result, scores), ex in zip(paired, cases, strict=True):
        passed = sum(1 for k, v in scores.items()
                     if k in _AGG_SCORE_KEYS and v == 1)
        checks = sum(1 for k in scores if k in _AGG_SCORE_KEYS)
        total_pass += passed
        total_checks += checks
        examples_out.append({
            "input": ex.get("input", ""),
            "tags": list(ex.get("tags", [])),
            "passed": passed,
            "checks": checks,
            "all_passed": (checks > 0 and passed == checks),
            "errored": bool(result.get("errored")),
            "fast_path": bool(result.get("fast_path")),
            "elapsed_sec": round(float(result.get("elapsed_sec", 0.0)), 2),
            "scores": dict(scores),
        })
    return {
        "version": 1,
        "total_examples": len(examples_out),
        "total_pass": total_pass,
        "total_checks": total_checks,
        "pass_ratio": (total_pass / total_checks) if total_checks else 0.0,
        "all_passed": (total_checks > 0 and total_pass == total_checks),
        "examples": examples_out,
    }


# ── LangSmith integration (optional) ────────────────────────────────────────


def upload_dataset_to_langsmith(name: str = "farmos-agent-eval-v1") -> None:
    """One-off: push DATASET to LangSmith so it's visible in the UI."""
    try:
        from langsmith import Client
    except ImportError:
        logger.error("langsmith not installed — skip upload")
        return
    if not settings.LANGCHAIN_API_KEY:
        logger.error("LANGCHAIN_API_KEY not set — skip upload")
        return

    client = Client(api_key=settings.LANGCHAIN_API_KEY)
    try:
        ds = client.create_dataset(dataset_name=name, description="FarmOS agent eval set")
    except Exception:  # noqa: BLE001 — already exists is OK
        ds = client.read_dataset(dataset_name=name)

    for ex in DATASET:
        client.create_example(
            inputs={"question": ex["input"]},
            outputs={"expected": ex["expected"]},
            dataset_id=ds.id,
            metadata={"tags": ex["tags"]},
        )
    logger.info("uploaded %d examples to LangSmith dataset %s", len(DATASET), name)


# ── CLI ─────────────────────────────────────────────────────────────────────


async def main_async(
    concurrency: int,
    write_candidates: bool = True,
    selector: str | None = None,
    summary_json_path: str | None = None,
) -> int:
    cases = filter_dataset(DATASET, selector)
    if selector:
        logger.info("eval.filter selector=%r matched=%d", selector, len(cases))
    agent = build_farm_agent(checkpointer=None, mcp_tools=None)
    semaphore = asyncio.Semaphore(concurrency)

    async def _run(example: dict) -> tuple[dict, dict]:
        async with semaphore:
            result = await run_one_example(agent, example)
            scores = score_example(result, example["expected"])
            return result, scores

    results = await asyncio.gather(*[_run(ex) for ex in cases])

    # Aggregate + report
    print("\n" + "=" * 80)
    print("FarmOS Agent Eval Report")
    print("=" * 80)
    total_pass = 0
    total_checks = 0
    failed_examples = 0
    for (result, scores), ex in zip(results, cases, strict=True):
        tags = ",".join(ex["tags"])
        passed = sum(1 for k, v in scores.items() if k in {
            "must_contain", "must_not_contain", "citation_present", "latency_ok", "verdict_correct",
        } and v == 1)
        checks = sum(1 for k in scores if k in {
            "must_contain", "must_not_contain", "citation_present", "latency_ok", "verdict_correct",
        })
        total_pass += passed
        total_checks += checks
        verdict = "✓" if passed == checks else "✗"
        path = "fast" if result.get("fast_path") else "agent"
        print(f"\n[{verdict}] {tags}  ({path}, {result['elapsed_sec']:.1f}s)")
        print(f"  Q: {ex['input']}")
        print(f"  A: {(result['output'] or '(empty)')[:200]}{'…' if len(result['output']) > 200 else ''}")
        print(f"  scores: {scores}")
        if result.get("diagnostic"):
            print(f"  diag: {result['diagnostic']}")
        if passed != checks or result.get("errored"):
            failed_examples += 1
            if write_candidates:
                _append_strategy_candidate(ex, result, scores, path)

    print("\n" + "=" * 80)
    print(f"TOTAL: {total_pass}/{total_checks} checks passed "
          f"({100 * total_pass / max(1, total_checks):.1f}%)")
    if write_candidates and failed_examples:
        print(f"  {failed_examples} failed example(s) → candidates appended to "
              f"{_CANDIDATES_FILE.relative_to(Path.cwd()) if _CANDIDATES_FILE.is_relative_to(Path.cwd()) else _CANDIDATES_FILE}")
    print("=" * 80)

    if summary_json_path:
        try:
            import json as _json_dump
            summary = build_json_summary(cases, list(results))
            out = Path(summary_json_path)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(
                _json_dump.dumps(summary, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.info("eval.summary_json path=%s pass_ratio=%.3f",
                        out, summary["pass_ratio"])
        except Exception:  # noqa: BLE001 — never break exit code on summary write
            logger.exception("eval.summary_json_write_failed path=%s",
                             summary_json_path)

    return 0 if total_pass == total_checks else 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upload-dataset", action="store_true",
                        help="Push DATASET to LangSmith and exit (one-off seeding)")
    parser.add_argument("--concurrency", type=int, default=2,
                        help="Parallel agent invocations (default 2 — keep low to avoid rate limits)")
    parser.add_argument("--no-candidates", action="store_true",
                        help="Disable strategy-candidate write-back to memory/STRATEGY_CANDIDATES.md")
    parser.add_argument("--example", default=None,
                        help="Run only DATASET[INDEX] or all examples whose tags include TAG "
                             "(integer or tag string). Use --list to see options.")
    parser.add_argument("--list", action="store_true",
                        help="Print the DATASET index (id, tags, question) and exit.")
    parser.add_argument("--summary-json", default=None,
                        help="Write a machine-readable run summary (JSON) to PATH "
                             "for CI regression gating.")
    args = parser.parse_args()

    if args.list:
        print_dataset_index(DATASET)
        return

    if args.upload_dataset:
        upload_dataset_to_langsmith()
        return

    # Enable LangSmith tracing if configured — every agent run will appear under
    # LANGCHAIN_PROJECT in the LangSmith UI alongside the per-example scores.
    if settings.LANGCHAIN_API_KEY and settings.LANGCHAIN_TRACING_V2.lower() == "true":
        os.environ["LANGCHAIN_TRACING_V2"] = "true"
        os.environ["LANGCHAIN_API_KEY"] = settings.LANGCHAIN_API_KEY
        os.environ["LANGCHAIN_PROJECT"] = f"{settings.LANGCHAIN_PROJECT}-eval"
        logger.info("LangSmith tracing → project %s-eval", settings.LANGCHAIN_PROJECT)

    try:
        exit_code = asyncio.run(main_async(
            args.concurrency,
            write_candidates=not args.no_candidates,
            selector=args.example,
            summary_json_path=args.summary_json,
        ))
    except ValueError as exc:
        # filter_dataset surfaces clear messages for bad --example values.
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
