"""Unified eval runner — runs all six surfaces and writes a single report.

Usage:
    cd backend
    uv run farm-agent-eval-all              # full suite
    uv run farm-agent-eval-all --fast       # routing + briefing format only (no LLM cost)
    uv run farm-agent-eval-all --surface routing,subsidy_calibration

Cost: full suite ~$4-5. Fast mode ~$0.
Reports land in backend/reports/agent_eval_<timestamp>.md.
"""

from __future__ import annotations

import argparse
import subprocess
import sys


SURFACE_TESTS = {
    "routing":            "tests/eval/test_routing.py",
    "tools":              "tests/eval/test_tools_smoke.py",
    "diagnosis":          "tests/eval/test_diagnosis_passthrough.py",
    "subsidy":            "tests/eval/test_subsidy_calibration.py",
    "farm_data":          "tests/eval/test_farm_data_faithfulness.py",
    "briefing_format":    "tests/eval/test_briefing_format.py",
    "trajectory_replay":  "tests/eval/test_trajectory_replay.py",
}

FAST_SURFACES = {"routing", "briefing_format"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run agent eval suite.")
    parser.add_argument("--fast", action="store_true", help="Skip LLM-cost tests (routing + briefing only)")
    parser.add_argument(
        "--surface",
        type=str,
        default="",
        help=f"Comma-separated list of surfaces to run. Available: {','.join(SURFACE_TESTS)}",
    )
    parser.add_argument(
        "--workers", "-n",
        type=str,
        default="4",
        help="Parallel worker count (pytest-xdist). 'auto' = CPU count, '0' = serial. Default: 4. "
             "Higher values speed up LLM-bound surfaces but may hit OpenRouter rate limits "
             "(429) on tight tiers. Drop to 2 if you see rate-limit errors.",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    if args.fast:
        targets = [SURFACE_TESTS[s] for s in FAST_SURFACES]
    elif args.surface:
        names = [s.strip() for s in args.surface.split(",") if s.strip()]
        unknown = [n for n in names if n not in SURFACE_TESTS]
        if unknown:
            print(f"unknown surface(s): {unknown}", file=sys.stderr)
            print(f"available: {list(SURFACE_TESTS)}", file=sys.stderr)
            return 2
        targets = [SURFACE_TESTS[n] for n in names]
    else:
        targets = list(SURFACE_TESTS.values())

    cmd = ["uv", "run", "pytest", *targets]
    # Parallel workers — xdist `-n N` runs each test in a separate worker process.
    # Disabled when workers="0" (useful for debugging a single failing test) or
    # when --fast (routing + briefing-format are too cheap to bother parallelizing).
    if args.workers != "0" and not args.fast:
        cmd.extend(["-n", args.workers])
        # Default distribution: load (per-test). Each worker gets individual
        # cases, so subsidy (40 cases, slowest surface) parallelizes across
        # workers. With -n 4, expected wall time ~5-7 min vs ~35 min serial.
        # Briefing's _BRIEFING_CONTENT_CACHE is per-worker — at most one extra
        # briefing generation per worker, ~$0.20 total cost overhead.
    if args.verbose:
        cmd.append("-v")
    cmd.append("--tb=short")

    print(f"[eval] running: {' '.join(cmd)}")
    result = subprocess.run(cmd, check=False)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
