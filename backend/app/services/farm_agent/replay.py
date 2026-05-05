"""Recorded-trace replay harness for farm-agent regression testing.

Why this exists:
    Live agent evals (Inspect AI) detect regressions on a *new* eval set,
    but they're expensive (LLM tokens + tool latency) and they don't
    capture the long tail of farmer queries that already exist in
    production traces. Replay testing snapshots a real production turn
    as a deterministic JSONL fixture, then re-runs CI against new code
    by stubbing every external call (LLM, tools, embedding). Token cost
    on the replay is zero. See AgentAssay (arXiv 2603.02601).

Sources of trace data:
    1. ``traces:tools:elided`` — Redis stream populated by
       ToolResultClearingMiddleware. Every dropped ToolMessage / tool-call
       AIMessage is XADDed here before eviction. Replay reconstructs the
       full message sequence.
    2. ``agent:bus:*`` — Redis Streams populated by farm_agent.bus. Every
       sub-agent fan-out (verifier debate, parallel candidates) leaves a
       submitted/running/done/failed entry sequence. Replay can reproduce
       the parallel-job outcomes deterministically.

What we record vs replay:
    Recording is *passive* — middlewares push to streams during normal
    operation. Replay reads the streams and constructs:
      * ``Trace`` — one logical turn (user_id + session_id + timestamp).
      * ``ReplayStub`` — for each external dep (LLM, tool, embedding),
        the stub returns the recorded result keyed on the call's input
        signature.

What this is NOT:
    A perfect replay. Non-deterministic features (timestamps, randomness,
    floating-point reductions) will drift. We accept that — the goal is
    catching *behavioural* regressions (different tool order, missing
    citation, wrong sub-agent dispatch), not bit-exact reproduction.

Usage in pytest:

    from app.services.farm_agent.replay import load_traces, replay_pytest

    @pytest.mark.parametrize("trace", load_traces("backend/tests/traces/"))
    async def test_subsidy_regression(trace):
        result = await replay_pytest(trace)
        assert result.matches_recorded(tolerance=0.85)
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.core.redis import get_redis

logger = logging.getLogger(__name__)


_DEFAULT_ELIDED_STREAM = "traces:tools:elided"


# ── Data model ──────────────────────────────────────────────────────────────


@dataclass
class TraceEntry:
    """One step of a recorded trace — message, tool call, or sub-agent step."""

    kind: str  # "message" | "tool_call" | "tool_result" | "subagent_step"
    role: str | None = None  # for messages: system/human/ai/tool
    content: str | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    tool_output: str | None = None
    subagent: str | None = None
    state: str | None = None  # subagent: submitted/running/done/failed
    ms: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Trace:
    """One complete agent turn, ready for deterministic replay."""

    id: str
    user_id: str | None
    session_id: str | None
    recorded_at: int  # epoch ms
    user_query: str
    final_answer: str | None
    entries: list[TraceEntry] = field(default_factory=list)

    def to_jsonl(self) -> str:
        # One trace per file for atomic writes; entries inside as a list.
        return json.dumps(asdict(self), ensure_ascii=False, default=str)

    @classmethod
    def from_jsonl(cls, line: str) -> "Trace":
        # `to_jsonl` writes one trace per file as a single JSON object — the
        # `.jsonl` extension is a misnomer. Strip surrounding whitespace so a
        # trailing newline doesn't trip strict parsers, and tolerate the
        # historical single-line case as the common path.
        d = json.loads(line.strip())
        d["entries"] = [TraceEntry(**e) for e in d.get("entries", [])]
        return cls(**d)


# ── Recording (read from Redis streams) ─────────────────────────────────────


async def harvest_elided_entries(
    *, count: int = 1000, stream: str = _DEFAULT_ELIDED_STREAM
) -> list[TraceEntry]:
    """Pull the latest ``count`` elided entries from the archive stream.

    Returns oldest-first (suitable for re-assembling a trace). Empty list
    if Redis is unavailable or the stream doesn't exist.
    """
    client = get_redis()
    if client is None:
        return []
    try:
        raw = await client.xrange(stream, count=count)
    except Exception as exc:  # noqa: BLE001
        logger.warning("replay.harvest_failed stream=%s err=%s", stream, exc)
        return []

    entries: list[TraceEntry] = []
    for entry_id, fields in raw or []:
        try:
            d = _flatten_fields(fields)
            kind = "tool_result" if d.get("type") == "ToolMessage" else "tool_call"
            content_raw = d.get("content", "")
            entries.append(
                TraceEntry(
                    kind=kind,
                    role="tool" if kind == "tool_result" else "ai",
                    content=content_raw,
                    tool_call_id=d.get("tool_call_id") or None,
                    metadata={"stream_id": str(entry_id), "type": d.get("type")},
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "replay.harvest_parse_failed id=%s err=%s", entry_id, exc
            )
    return entries


async def harvest_bus_entries(
    *, agent_type: str, count: int = 500
) -> list[TraceEntry]:
    """Pull sub-agent fan-out journal entries for a given agent_type.

    Reads ``agent:bus:{agent_type}``. Returns oldest-first.
    """
    client = get_redis()
    if client is None:
        return []
    try:
        raw = await client.xrange(f"agent:bus:{agent_type}", count=count)
    except Exception as exc:  # noqa: BLE001
        logger.warning("replay.bus_harvest_failed agent=%s err=%s", agent_type, exc)
        return []

    entries: list[TraceEntry] = []
    for entry_id, fields in raw or []:
        d = _flatten_fields(fields)
        try:
            ms = int(d.get("ms", 0))
        except (TypeError, ValueError):
            ms = 0
        entries.append(
            TraceEntry(
                kind="subagent_step",
                subagent=agent_type,
                state=d.get("state"),
                content=d.get("result") or d.get("payload") or d.get("error"),
                ms=ms,
                metadata={"stream_id": str(entry_id), "job_id": d.get("job_id")},
            )
        )
    return entries


def _flatten_fields(fields: Any) -> dict[str, str]:
    """XRANGE field shape varies between text/bytes mode and redis-py versions.

    Normalise to a plain dict[str, str].
    """
    if isinstance(fields, dict):
        out: dict[str, str] = {}
        for k, v in fields.items():
            ks = k.decode("utf-8") if isinstance(k, bytes) else str(k)
            vs = v.decode("utf-8") if isinstance(v, bytes) else str(v)
            out[ks] = vs
        return out
    if isinstance(fields, list):
        out2: dict[str, str] = {}
        for i in range(0, len(fields) - 1, 2):
            k = fields[i]
            v = fields[i + 1]
            ks = k.decode("utf-8") if isinstance(k, bytes) else str(k)
            vs = v.decode("utf-8") if isinstance(v, bytes) else str(v)
            out2[ks] = vs
        return out2
    return {}


# ── On-disk fixture I/O ─────────────────────────────────────────────────────


def save_trace(trace: Trace, dir_path: str | Path) -> Path:
    """Write a Trace to ``dir_path/{trace.id}.jsonl``. Creates dir if missing."""
    p = Path(dir_path)
    p.mkdir(parents=True, exist_ok=True)
    out = p / f"{trace.id}.jsonl"
    out.write_text(trace.to_jsonl(), encoding="utf-8")
    return out


def load_traces(dir_path: str | Path) -> list[Trace]:
    """Load every ``*.jsonl`` Trace fixture from a directory.

    Returns empty list if the dir doesn't exist (so pytest collection
    doesn't crash on a fresh checkout).
    """
    p = Path(dir_path)
    if not p.exists():
        return []
    traces: list[Trace] = []
    for f in sorted(p.glob("*.jsonl")):
        try:
            traces.append(Trace.from_jsonl(f.read_text(encoding="utf-8")))
        except Exception as exc:  # noqa: BLE001
            logger.warning("replay.load_failed file=%s err=%s", f, exc)
    return traces


# ── Stub layer: deterministic replay of recorded calls ──────────────────────


@dataclass
class StubLookupMiss:
    """Marker returned when a stub has no recorded answer for the input."""

    reason: str
    key: str


class ReplayStub:
    """Lookup table for deterministic replay of an external call.

    Keys are computed from the call's significant inputs (e.g. tool name +
    sorted JSON args). Misses return ``StubLookupMiss`` so the test can
    decide whether to fail strictly or fall back to a real call.
    """

    def __init__(self) -> None:
        self._table: dict[str, Any] = {}

    def record(self, key: str, value: Any) -> None:
        self._table[key] = value

    def lookup(self, key: str) -> Any:
        if key not in self._table:
            return StubLookupMiss(reason="not_recorded", key=key)
        return self._table[key]

    @staticmethod
    def tool_key(tool_name: str, args: dict[str, Any] | None) -> str:
        return f"tool:{tool_name}:{json.dumps(args or {}, sort_keys=True, ensure_ascii=False)}"

    @staticmethod
    def llm_key(messages: list[Any]) -> str:
        # Hash on the textified message sequence — order-sensitive but
        # tolerant of object-shape drift.
        import hashlib

        sig = json.dumps(
            [(getattr(m, "type", type(m).__name__), str(getattr(m, "content", "")))
             for m in messages],
            ensure_ascii=False,
            default=str,
        )
        return f"llm:{hashlib.sha256(sig.encode()).hexdigest()[:32]}"


def build_stub_from_trace(trace: Trace) -> ReplayStub:
    """Construct a ReplayStub from a Trace's recorded entries.

    Currently records tool_call → tool_result pairs by tool_call_id.
    Extend as you record more dimensions (LLM hashes, embedding outputs).
    """
    stub = ReplayStub()
    pending_calls: dict[str, TraceEntry] = {}
    for entry in trace.entries:
        if entry.kind == "tool_call" and entry.tool_call_id:
            pending_calls[entry.tool_call_id] = entry
        elif entry.kind == "tool_result" and entry.tool_call_id:
            call = pending_calls.pop(entry.tool_call_id, None)
            if call and call.tool_name:
                key = ReplayStub.tool_key(call.tool_name, call.tool_args)
                stub.record(key, entry.tool_output or entry.content or "")
    return stub


# ── Diff & comparison ──────────────────────────────────────────────────────


@dataclass
class TraceComparison:
    """Result of comparing a fresh run against a recorded Trace."""

    matched_tools: int = 0
    missing_tools: list[str] = field(default_factory=list)
    extra_tools: list[str] = field(default_factory=list)
    answer_similarity: float = 0.0
    notes: list[str] = field(default_factory=list)

    def matches_recorded(self, *, tolerance: float = 0.85) -> bool:
        """Heuristic pass/fail for pytest assertions."""
        if self.missing_tools:
            return False
        return self.answer_similarity >= tolerance


def compare_runs(
    *,
    recorded: Trace,
    fresh_tool_sequence: list[tuple[str, dict[str, Any]]],
    fresh_answer: str,
) -> TraceComparison:
    """Compare a fresh agent run's tool sequence + answer to a recorded trace.

    Tool sequence is order-tolerant (set comparison) — order regressions
    are flagged in notes but don't fail the assertion. Answer similarity
    is char-trigram Jaccard (cheap, language-agnostic, no extra deps).
    """
    cmp = TraceComparison()

    recorded_tools = [
        e.tool_name for e in recorded.entries if e.kind == "tool_call" and e.tool_name
    ]
    fresh_tool_names = [t[0] for t in fresh_tool_sequence]
    rec_set = set(recorded_tools)
    fresh_set = set(fresh_tool_names)
    cmp.matched_tools = len(rec_set & fresh_set)
    cmp.missing_tools = sorted(rec_set - fresh_set)
    cmp.extra_tools = sorted(fresh_set - rec_set)
    if recorded_tools != fresh_tool_names:
        cmp.notes.append(
            f"tool_order_drift recorded={recorded_tools} fresh={fresh_tool_names}"
        )

    cmp.answer_similarity = _trigram_jaccard(
        recorded.final_answer or "", fresh_answer or ""
    )
    return cmp


def _trigram_jaccard(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    ta = {a[i : i + 3] for i in range(len(a) - 2)}
    tb = {b[i : i + 3] for i in range(len(b) - 2)}
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


# ── High-level: harvest a trace from live Redis streams ────────────────────


async def harvest_trace_from_redis(
    *,
    trace_id: str,
    user_query: str,
    final_answer: str | None = None,
    user_id: str | None = None,
    session_id: str | None = None,
    include_subagents: tuple[str, ...] = ("verifier",),
) -> Trace:
    """Build a Trace by harvesting all relevant Redis streams.

    Convenience for an admin endpoint that, after a notable production
    turn, snapshots the live streams to a JSONL fixture for later
    regression testing.
    """
    import time as _time

    entries: list[TraceEntry] = []
    entries.extend(await harvest_elided_entries(count=2000))
    for sub in include_subagents:
        entries.extend(await harvest_bus_entries(agent_type=sub, count=500))

    return Trace(
        id=trace_id,
        user_id=user_id,
        session_id=session_id,
        recorded_at=int(_time.time() * 1000),
        user_query=user_query,
        final_answer=final_answer,
        entries=entries,
    )
