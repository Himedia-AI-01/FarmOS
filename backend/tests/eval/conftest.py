"""Shared fixtures + utilities for the eval harness.

Loads JSONL datasets, provides per-surface DataFrame-style iteration, and
exposes a `report_writer` fixture that captures pass/fail rows for the final
markdown report.

LangSmith integration: when LANGCHAIN_API_KEY is set in settings, every eval
LLM call gets traced under a dedicated `LANGCHAIN_PROJECT` so eval runs are
separate from production traces. Web dashboard: smith.langchain.com → that
project shows full tool-call trees, prompts, and LLM responses for every case.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import pytest

# Inject LangSmith env vars from settings so eval LLM calls get traced.
# LangChain / langgraph reads these from os.environ directly. Eval runs land
# in a dedicated project (suffixed "-eval") to keep prod traces clean.
try:
    from app.core.config import settings as _s

    if _s.LANGCHAIN_API_KEY:
        os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")
        os.environ.setdefault("LANGCHAIN_API_KEY", _s.LANGCHAIN_API_KEY)
        os.environ.setdefault("LANGCHAIN_ENDPOINT", _s.LANGCHAIN_ENDPOINT or "https://api.smith.langchain.com")
        # Override project for eval runs.
        os.environ["LANGCHAIN_PROJECT"] = f"{_s.LANGCHAIN_PROJECT or 'farmos-agent'}-eval"
except Exception:
    pass  # settings unavailable → skip tracing silently

DATASETS_DIR = Path(__file__).parent / "datasets"
REPORTS_DIR = Path(__file__).resolve().parents[2] / "reports"


def load_jsonl(name: str) -> list[dict[str, Any]]:
    """Read a dataset JSONL by base name (without .jsonl extension)."""
    path = DATASETS_DIR / f"{name}.jsonl"
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        rows.append(json.loads(line))
    return rows


@pytest.fixture(scope="session")
def report_path() -> Path:
    """Markdown report path — one per session, timestamp-stamped."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return REPORTS_DIR / f"agent_eval_{ts}.md"


_RESULT_LOG: list[dict[str, Any]] = []

# Cross-worker result aggregation. Each xdist worker writes to its OWN file
# named .eval_inflight_<workerid>.jsonl. Master reads all matching files and
# consolidates. Per-worker files avoid Windows file-append race conditions
# (concurrent appends without OS-level locking can drop lines on Windows).
_INFLIGHT_DIR = REPORTS_DIR
_INFLIGHT_PREFIX = ".eval_inflight_"


def _is_xdist_worker(config) -> bool:
    """True when running inside a pytest-xdist worker (not the master process)."""
    return hasattr(config, "workerinput")


def _worker_id(config) -> str:
    """Unique id for this process. xdist workers get 'gw0', 'gw1', etc.;
    serial / master process gets 'master'."""
    if hasattr(config, "workerinput"):
        return config.workerinput.get("workerid", "unknown")
    return "master"


def _inflight_path_for(worker_id: str) -> Path:
    return _INFLIGHT_DIR / f"{_INFLIGHT_PREFIX}{worker_id}.jsonl"


# Track our own worker id so record_result knows which file to write to.
_THIS_WORKER_ID = {"id": "master"}


@pytest.fixture
def record_result(request):
    """Append a result row visible in the final report. Per-worker file."""
    def _append(**kwargs):
        row = {"ts": datetime.now(timezone.utc).isoformat(), **kwargs}
        _RESULT_LOG.append(row)
        # Mark this test node as having recorded its own result so the
        # makereport hook doesn't double-synthesize on later teardown errors.
        _TESTS_WITH_RESULTS.add(request.node.nodeid)
        try:
            path = _inflight_path_for(_THIS_WORKER_ID["id"])
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except Exception:
            pass
    return _append


def pytest_configure(config):
    """Master: wipe ALL prior inflight worker files (clean slate for new run).
    Worker: just record its id so record_result writes to the right file."""
    _THIS_WORKER_ID["id"] = _worker_id(config)
    if not _is_xdist_worker(config):
        try:
            for old in _INFLIGHT_DIR.glob(f"{_INFLIGHT_PREFIX}*.jsonl"):
                old.unlink()
        except Exception:
            pass


# Track which test items called record_result so we can detect "errored before
# reaching record_result" cases (test crashed mid-run, e.g. LLM timeout/error).
_TESTS_WITH_RESULTS: set[str] = set()


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Capture errored/crashed tests that didn't reach record_result.

    Without this, a test that raises mid-run (LLM timeout, network blip) shows
    in pytest stdout as ERROR but never writes a row to the inflight file →
    silently missing from the report. We catch the call.excinfo here and
    synthesize a record for any test in the eval/ folder that didn't already
    register its own result.
    """
    outcome = yield
    report = outcome.get_result()

    # Only synthesize on failed/errored "call" phase; ignore setup/teardown.
    if report.when != "call" or report.outcome == "passed":
        return
    nodeid = item.nodeid
    if nodeid in _TESTS_WITH_RESULTS:
        return  # test already recorded its own (more detailed) result
    if "tests/eval/" not in nodeid and "tests\\eval\\" not in nodeid:
        return

    # Synthesize a failure row so the missing cases appear in the report.
    surface = "unknown"
    case_id = nodeid.split("::")[-1][:60]
    if "test_subsidy" in nodeid:
        surface = "subsidy_calibration"
    elif "test_diagnosis" in nodeid:
        surface = "diagnosis_passthrough"
    elif "test_farm_data" in nodeid:
        surface = "farm_data_faithfulness"
    elif "test_briefing" in nodeid:
        surface = "briefing_format"
    elif "test_tools" in nodeid:
        surface = "tools"
    elif "test_routing" in nodeid:
        surface = "routing"
    elif "test_trajectory" in nodeid:
        surface = "trajectory_replay"

    err_text = ""
    if call.excinfo is not None:
        err_text = f"{call.excinfo.type.__name__}: {str(call.excinfo.value)[:300]}"

    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "surface": surface,
        "case_id": case_id,
        "passed": False,
        "detail": f"TEST_ERRORED: {err_text}" if err_text else "TEST_ERRORED",
        "query": "(test crashed before recording result)",
        "answer": err_text,
    }
    try:
        path = _inflight_path_for(_THIS_WORKER_ID["id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        _RESULT_LOG.append(row)
    except Exception:
        pass


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001
    """Write a markdown report after all tests finish.

    Only the master process writes the final report. Workers skip — they've
    already appended their results to _AGGREGATE_PATH via record_result.
    The master reads ALL workers' rows from that JSONL and consolidates.

    Format:
      ## <surface> — N/M (X%)
        ### Failures
          - **case_id** detail
            <details><summary>query/answer</summary> truncated text</details>
        ### Passes (collapsed)
    """
    if _is_xdist_worker(session.config):
        return  # workers don't write reports — only the master aggregates

    # Read rows from ALL per-worker inflight files (xdist) plus master's own
    # (serial mode). Per-worker file pattern avoids Windows append-race drops.
    rows: list[dict[str, Any]] = []
    try:
        for inflight in _INFLIGHT_DIR.glob(f"{_INFLIGHT_PREFIX}*.jsonl"):
            for line in inflight.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception:
        pass

    # Fallback to in-memory log if file read failed.
    if not rows:
        rows = list(_RESULT_LOG)
    if not rows:
        return

    # Cleanup inflight files now that we've consumed them.
    try:
        for f in _INFLIGHT_DIR.glob(f"{_INFLIGHT_PREFIX}*.jsonl"):
            f.unlink()
    except Exception:
        pass

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out = REPORTS_DIR / f"agent_eval_{ts}.md"

    by_surface: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_surface.setdefault(r.get("surface", "?"), []).append(r)

    project = os.environ.get("LANGCHAIN_PROJECT", "")
    lines = [f"# Agent Eval Report — {ts}", ""]
    if project:
        lines.append(f"LangSmith project: **{project}** (smith.langchain.com)")
        lines.append("")

    for surface, rows in sorted(by_surface.items()):
        passed = sum(1 for r in rows if r.get("passed"))
        total = len(rows)
        rate = passed / total * 100 if total else 0
        lines.append(f"## {surface} — {passed}/{total} ({rate:.0f}%)")
        lines.append("")

        # Failures first — surface them prominently with full context.
        failures = [r for r in rows if not r.get("passed")]
        passes = [r for r in rows if r.get("passed")]

        if failures:
            lines.append(f"### ❌ Failures ({len(failures)})")
            lines.append("")
            for r in failures:
                lines.append(f"- **`{r.get('case_id', '?')}`** — {r.get('detail', '')}")
                if r.get("query"):
                    lines.append(f"  - Query: `{r['query'][:200]}`")
                if r.get("answer"):
                    ans = r["answer"]
                    # Use a collapsible block so long answers don't drown the report.
                    lines.append("  <details><summary>Answer</summary>")
                    lines.append("")
                    lines.append("  ```")
                    lines.append("  " + ans[:1500].replace("\n", "\n  "))
                    if len(ans) > 1500:
                        lines.append(f"  … (truncated, total {len(ans)} chars)")
                    lines.append("  ```")
                    lines.append("  </details>")
            lines.append("")

        if passes:
            lines.append(f"### ✅ Passes ({len(passes)} — collapsed)")
            lines.append("")
            lines.append("<details><summary>Show passing cases</summary>")
            lines.append("")
            for r in passes:
                lines.append(f"- ✅ `{r.get('case_id', '?')}` — {r.get('detail', '')}")
            lines.append("")
            lines.append("</details>")
            lines.append("")

    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n[eval] report written → {out}")
    if project:
        print(f"[eval] LangSmith traces → project '{project}' at smith.langchain.com")
