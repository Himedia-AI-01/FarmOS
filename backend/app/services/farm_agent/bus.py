"""Redis Streams fan-out bus for sub-agent parallelism.

What this is for:
    The orchestrator increasingly needs to run N copies of a sub-agent in
    parallel and fuse the results — concrete near-term uses:

      * **Verifier debate** (methodology roadmap #1): 2–3 verifier judges
        score the same draft and we halt when their distributions stabilise.
      * **MaTTS contrastive rollouts** (methodology roadmap A): k=3 parallel
        trajectories on the same query, pick the best, distil the rest.
      * **Candidate synthesis**: subsidy-agent generates 3 phrasings of the
        same answer; CRAG re-ranker picks the highest-faithfulness one.

What this is NOT:
    A general-purpose task queue. We don't try to compete with Celery,
    RQ, or Dramatiq. We only need:
      1. **Speed**: in-process `asyncio.gather` for the actual concurrency.
      2. **Observability**: every dispatched job is journaled to a Redis
         Stream so XLEN gives queue depth and XRANGE replays history.
      3. **Soft durability**: a worker crash is logged; the Stream entry
         carries enough info to retry from a sibling process. We do NOT
         require at-least-once across crashes — the orchestrator is the
         job of LangGraph's checkpointer (Postgres) above us.

Stream layout (one stream per agent type, e.g. ``agent:bus:verifier``):
    XADD agent:bus:verifier  *  job_id=<hex16> trace=<trace_id> state=submitted payload=<json>
    XADD agent:bus:verifier  *  job_id=<hex16> state=running
    XADD agent:bus:verifier  *  job_id=<hex16> state=done   result=<json>  ms=<int>
    XADD agent:bus:verifier  *  job_id=<hex16> state=failed error=<str>   ms=<int>

We auto-trim each stream with MAXLEN ~ 10000 so it stays bounded — the
checkpointer is the source of truth, this is just an observability log.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from app.core.redis import get_redis

logger = logging.getLogger(__name__)


T = TypeVar("T")  # job input
R = TypeVar("R")  # job result


_STREAM_PREFIX = "agent:bus:"
_STREAM_MAXLEN = 10_000  # ~ approx — Redis trims efficiently with `~`


# ── Result envelope ─────────────────────────────────────────────────────────


@dataclass
class FanoutResult(Generic[R]):
    """Outcome of a single parallel job."""

    job_id: str
    ok: bool
    result: R | None = None
    error: str | None = None
    ms: int = 0


@dataclass
class FanoutSummary(Generic[R]):
    """Outcome of an entire parallel_fanout call."""

    results: list[FanoutResult[R]] = field(default_factory=list)
    timed_out: bool = False
    total_ms: int = 0

    @property
    def successes(self) -> list[R]:
        return [r.result for r in self.results if r.ok and r.result is not None]

    @property
    def n_ok(self) -> int:
        return sum(1 for r in self.results if r.ok)

    @property
    def n_failed(self) -> int:
        return sum(1 for r in self.results if not r.ok)


# ── Public API ──────────────────────────────────────────────────────────────


async def parallel_fanout(
    *,
    agent_type: str,
    payloads: Sequence[T],
    worker: Callable[[T], Awaitable[R]],
    timeout_sec: float | None = None,
    max_concurrency: int | None = None,
    trace_id: str | None = None,
) -> FanoutSummary[R]:
    """Run ``worker(p)`` for every p in payloads concurrently.

    Each job is journaled to ``agent:bus:{agent_type}`` (submitted / done /
    failed). When Redis is unavailable the journaling silently no-ops —
    fan-out itself still runs (this is best-effort observability, not
    durability).

    Args:
        agent_type: short name routing to the stream (e.g. ``verifier``,
            ``subsidy-candidate``). Should be one of the named sub-agents
            so XLEN per stream maps to per-domain queue depth.
        payloads: inputs handed one-at-a-time to ``worker``.
        worker: async callable returning a result. Exceptions are caught
            and surfaced as ``FanoutResult(ok=False, error=...)`` — never
            raised out of this function.
        timeout_sec: wall-clock cap. On timeout we cancel pending jobs and
            return what we have with ``timed_out=True``. None means wait
            forever (only safe when callers have their own deadline).
        max_concurrency: cap on simultaneously-running jobs. None means
            run all in parallel (safe for small fan-outs ≤16; raise it
            explicitly for larger).
        trace_id: optional correlation id stamped onto every stream entry
            so logs/replays can tie together a parent run with its
            children. If omitted we generate one.

    Returns:
        ``FanoutSummary`` with per-job ``FanoutResult`` and aggregate
        flags. Order matches ``payloads``.
    """
    if not payloads:
        return FanoutSummary[R](results=[], timed_out=False, total_ms=0)

    trace_id = trace_id or _short_id()
    stream_key = f"{_STREAM_PREFIX}{agent_type}"
    redis_client = get_redis()  # may be None — journaling becomes a no-op

    # Per-job ids upfront so the journal references match the result order.
    job_ids = [_short_id() for _ in payloads]

    # Pre-emit `submitted` for all jobs in a pipeline so the queue depth
    # reflects reality before any worker starts. Best-effort.
    if redis_client is not None:
        try:
            pipe = redis_client.pipeline(transaction=False)
            for jid, payload in zip(job_ids, payloads, strict=False):
                pipe.xadd(
                    stream_key,
                    _entry_fields(
                        job_id=jid,
                        trace=trace_id,
                        state="submitted",
                        payload=_json_safe(payload),
                    ),
                    maxlen=_STREAM_MAXLEN,
                    approximate=True,
                )
            await pipe.execute()
        except Exception as exc:  # noqa: BLE001 — journaling failure is non-fatal
            logger.warning("bus.journal_submit_failed agent=%s err=%s", agent_type, exc)

    semaphore = asyncio.Semaphore(max_concurrency) if max_concurrency else None
    t0 = time.perf_counter()

    async def _wrapped(jid: str, payload: T) -> FanoutResult[R]:
        start = time.perf_counter()

        async def _run_inner() -> FanoutResult[R]:
            try:
                if redis_client is not None:
                    try:
                        await redis_client.xadd(
                            stream_key,
                            _entry_fields(job_id=jid, trace=trace_id, state="running"),
                            maxlen=_STREAM_MAXLEN,
                            approximate=True,
                        )
                    except Exception:  # noqa: BLE001
                        pass
                result = await worker(payload)
            except asyncio.CancelledError:
                # Propagate so gather sees the cancel and ends fast.
                raise
            except Exception as exc:  # noqa: BLE001 — caught and reported
                ms = int((time.perf_counter() - start) * 1000)
                msg = f"{type(exc).__name__}: {exc}"
                if redis_client is not None:
                    try:
                        await redis_client.xadd(
                            stream_key,
                            _entry_fields(
                                job_id=jid,
                                trace=trace_id,
                                state="failed",
                                error=msg[:500],
                                ms=str(ms),
                            ),
                            maxlen=_STREAM_MAXLEN,
                            approximate=True,
                        )
                    except Exception:  # noqa: BLE001
                        pass
                logger.warning(
                    "bus.job_failed agent=%s job=%s ms=%d err=%s",
                    agent_type, jid, ms, msg,
                )
                return FanoutResult(job_id=jid, ok=False, error=msg, ms=ms)

            ms = int((time.perf_counter() - start) * 1000)
            if redis_client is not None:
                try:
                    await redis_client.xadd(
                        stream_key,
                        _entry_fields(
                            job_id=jid,
                            trace=trace_id,
                            state="done",
                            result=_json_safe(result),
                            ms=str(ms),
                        ),
                        maxlen=_STREAM_MAXLEN,
                        approximate=True,
                    )
                except Exception:  # noqa: BLE001
                    pass
            return FanoutResult(job_id=jid, ok=True, result=result, ms=ms)

        if semaphore is None:
            return await _run_inner()
        async with semaphore:
            return await _run_inner()

    # Wrap as Tasks (not bare coroutines) so we can inspect which finished and
    # which were still pending on a timeout — `asyncio.wait_for(gather(...))`
    # would cancel everything and lose successful results.
    tasks = [
        asyncio.create_task(_wrapped(jid, p))
        for jid, p in zip(job_ids, payloads, strict=False)
    ]
    timed_out = False

    if timeout_sec is None:
        results_raw = await asyncio.gather(*tasks)
    else:
        done, pending = await asyncio.wait(tasks, timeout=timeout_sec)
        if pending:
            timed_out = True
            logger.warning(
                "bus.fanout_timeout agent=%s n=%d done=%d pending=%d timeout=%.1fs",
                agent_type, len(payloads), len(done), len(pending), timeout_sec,
            )
            for t in pending:
                t.cancel()
            # Drain cancellations so they don't surface as "Task was destroyed".
            await asyncio.gather(*pending, return_exceptions=True)
        results_raw = []
        for jid, t in zip(job_ids, tasks, strict=True):
            if t in pending:
                results_raw.append(FanoutResult(job_id=jid, ok=False, error="timeout"))
                continue
            exc = t.exception()
            if exc is not None:
                results_raw.append(
                    FanoutResult(job_id=jid, ok=False, error=f"{type(exc).__name__}: {exc}")
                )
            else:
                results_raw.append(t.result())

    total_ms = int((time.perf_counter() - t0) * 1000)
    return FanoutSummary[R](results=list(results_raw), timed_out=timed_out, total_ms=total_ms)


# ── Stream introspection (for /health, ops dashboards) ──────────────────────


async def queue_depth(agent_type: str) -> int | None:
    """Approximate queue depth for the named stream. None if Redis disabled.

    "Depth" here is just the stream length — useful as a coarse signal of
    fan-out throughput. Approximate because of MAXLEN ~ trimming.
    """
    client = get_redis()
    if client is None:
        return None
    try:
        return int(await client.xlen(f"{_STREAM_PREFIX}{agent_type}"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("bus.xlen_failed agent=%s err=%s", agent_type, exc)
        return None


async def recent_entries(
    agent_type: str, *, count: int = 50
) -> list[dict[str, Any]]:
    """Last ``count`` stream entries for replay/debugging. Empty if disabled."""
    client = get_redis()
    if client is None:
        return []
    try:
        raw = await client.xrevrange(f"{_STREAM_PREFIX}{agent_type}", count=count)
    except Exception as exc:  # noqa: BLE001
        logger.warning("bus.xrevrange_failed agent=%s err=%s", agent_type, exc)
        return []

    out: list[dict[str, Any]] = []
    for entry_id, fields in raw or []:
        # Build the field dict first, then attach the stream entry id LAST so
        # a worker that wrote a `job_id`/`id` field of its own can't shadow
        # the stream entry id (which downstream replay uses for ordering).
        try:
            d = {k: v for k, v in fields.items()}
        except AttributeError:
            # bytes-mode — fields is a list[k,v,k,v]
            d = {}
            for i in range(0, len(fields) - 1, 2):
                d[str(fields[i])] = fields[i + 1]
        d["id"] = entry_id
        out.append(d)
    return out


# ── Helpers ─────────────────────────────────────────────────────────────────


def _short_id() -> str:
    return secrets.token_hex(8)


def _entry_fields(**kwargs: Any) -> dict[str, str]:
    """XADD requires str/bytes values — coerce defensively."""
    return {k: str(v) for k, v in kwargs.items() if v is not None}


def _json_safe(value: Any, *, max_len: int = 4000) -> str:
    """Serialise to JSON with a hard length cap so a giant payload doesn't
    bloat the stream. We're observability, not storage of record."""
    try:
        s = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001
        s = repr(value)
    if len(s) > max_len:
        return s[:max_len] + f"…[truncated {len(s) - max_len}b]"
    return s


# ── High-level convenience: fixed-fanout helper for verifier debate ────────


async def parallel_verifier_debate(
    *,
    draft: str,
    judges: Iterable[Callable[[str], Awaitable[dict[str, Any]]]],
    timeout_sec: float = 15.0,
    trace_id: str | None = None,
) -> FanoutSummary[dict[str, Any]]:
    """Convenience wrapper for the verifier-debate pattern.

    Pass N judge callables (each takes the draft, returns a verdict dict
    with at least ``score`` and ``rationale``). We run them in parallel,
    journal each to ``agent:bus:verifier``, and return all verdicts.
    Halt-when-stable is the caller's responsibility (see methodology
    roadmap #1 — Multi-Agent Debate with Adaptive Stability).
    """
    judges_list = list(judges)
    if not judges_list:
        return FanoutSummary[dict[str, Any]]()

    async def _run(judge: Callable[[str], Awaitable[dict[str, Any]]]) -> dict[str, Any]:
        return await judge(draft)

    return await parallel_fanout(
        agent_type="verifier",
        payloads=judges_list,
        worker=_run,
        timeout_sec=timeout_sec,
        max_concurrency=len(judges_list),  # full parallel — debates are small
        trace_id=trace_id,
    )
