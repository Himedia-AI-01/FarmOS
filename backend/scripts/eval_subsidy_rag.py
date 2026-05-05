"""Subsidy RAG accuracy + latency benchmark.

Loads backend/tests/subsidy_eval/eval_set.json and runs every question through
both `GovSubsidyRAG.search()` (full hybrid + rerank) and `search_fast()`
(dense-only). Records:

    Retrieval quality
      - clause_hit@1, @3, @5  : did any expected clause appear in top-k?
      - keyword_recall@1      : fraction of expected keywords in top-1 snippet
      - mrr                   : reciprocal rank of first expected clause hit

    Speed
      - latency_ms (per question, p50/p95 aggregate)

Usage:
    cd backend
    uv run python scripts/eval_subsidy_rag.py
    uv run python scripts/eval_subsidy_rag.py --mode fast      # only search_fast
    uv run python scripts/eval_subsidy_rag.py --mode full      # only search
    uv run python scripts/eval_subsidy_rag.py --top-k 5 --out reports/

Output:
    reports/subsidy_rag_eval_<timestamp>.json   — raw per-question results
    reports/subsidy_rag_eval_<timestamp>.md     — human-readable summary

Design notes:
    - Clause id derivation matches gov_rag.get_clauses_index(): Roman from
      Citation.chapter, Arabic from Citation.article. e.g. "II.기본직불금..."
      + "3. 소농직불 지급대상 자격요건" → "II-3".
    - Chapter 2 준수사항 sub-items (영농기록 등) live under different roman
      numerals; we additionally accept C2-<roman> matches when the chapter
      string contains "준수사항 이행점검" or matches by subsection_title heuristic.
    - We do NOT rebuild/reindex — eval assumes `subsidy-ingest` has run.
      (Run `uv run subsidy-ingest` first if collection is empty.)
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


ROMAN_RE = re.compile(r"\b([IVXLCDM]+)\.")
ARABIC_RE = re.compile(r"^\s*(\d+)\.")
# Direct clause-id forms emitted by the Redis backend's Citation:
#   article="II-3", "C2-VI", "BP4", or sometimes the sub_path "가.①" if no clause_id.
DIRECT_CLAUSE_RE = re.compile(r"^((?:C2-)?[IVXLCDM]+(?:-\d+)?)$")
DIRECT_BP_RE = re.compile(r"^(BP\d+)$")

# 준수사항 이행점검 지침 (Chapter 2) section labels seen in subsection metadata.
C2_TITLES = {
    "농지": "C2-I",
    "농약": "C2-II",
    "화학비료": "C2-III",
    "교육": "C2-IV",
    "영농폐기물": "C2-V",
    "영농기록": "C2-VI",
    "농업경영체": "C2-VII",
    "기타": "C2-VIII",
}


def _derive_clause_ids(citation) -> set[str]:
    """Citation → set of plausible clause ids the eval can match against.

    Handles both backends:
      Chroma path  → article='3. 소농직불...', chapter='II. 기본직불금...'
                     (Roman from chapter, Arabic from article)
      Redis path   → article='II-3', chapter='II-3'  (clause_id directly)
    """
    ids: set[str] = set()

    chapter = getattr(citation, "chapter", "") or ""
    article = getattr(citation, "article", "") or ""

    # ── Redis backend: article/chapter is already the clean clause id ─────
    for field in (article, chapter):
        m = DIRECT_CLAUSE_RE.match(field.strip())
        if m:
            ids.add(m.group(1))
        m = DIRECT_BP_RE.match(field.strip())
        if m:
            ids.add(m.group(1))

    # ── Chroma backend: Roman.dot + Arabic.dot composition ──────────────
    rm = ROMAN_RE.search(chapter)
    am = ARABIC_RE.match(article)
    if rm and am:
        ids.add(f"{rm.group(1)}-{am.group(1)}")
    elif rm and not ids:
        # Only fallback to roman-only if we don't already have a direct id
        ids.add(rm.group(1))

    # ── Chapter 2 준수사항 keyword fallback (both backends) ─────────────
    blob = f"{article} {chapter} {getattr(citation, 'snippet', '') or ''}"
    for kw, c2 in C2_TITLES.items():
        if kw in blob:
            ids.add(c2)
            ids.add("II-6")  # 준수사항 모자조항
            break

    return ids


def _keyword_recall(snippet: str, expected_keywords: list[str]) -> float:
    if not expected_keywords:
        return 1.0
    hits = sum(1 for k in expected_keywords if k in (snippet or ""))
    return hits / len(expected_keywords)


def _hit_at_k(citations, expected: set[str], k: int) -> bool:
    for c in citations[:k]:
        derived = _derive_clause_ids(c)
        if derived & expected:
            return True
    return False


def _first_hit_rank(citations, expected: set[str]) -> int | None:
    for i, c in enumerate(citations, start=1):
        if _derive_clause_ids(c) & expected:
            return i
    return None


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * p
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


def _run_one(rag, mode: str, query: str, top_k: int) -> tuple[list, float]:
    t0 = time.perf_counter()
    if mode == "fast":
        cits = rag.search_fast(query, top_k=top_k)
    else:
        cits = rag.search(query, top_k=top_k)
    elapsed = (time.perf_counter() - t0) * 1000
    return cits, elapsed


def evaluate(eval_set: dict, modes: list[str], top_k: int) -> dict[str, Any]:
    # Lazy import: importing GovSubsidyRAG triggers Solar/CrossEncoder lazy load
    # but the latter only on first .search() — not on construction.
    from app.services.subsidy.gov_rag import GovSubsidyRAG

    rag = GovSubsidyRAG()
    if rag.count() == 0:
        logger.error(
            "Collection 'gov_subsidy' is empty. Run `uv run subsidy-ingest` first."
        )
        sys.exit(2)
    logger.info(f"인덱스 청크 수: {rag.count()}")

    questions = eval_set["questions"]
    results: dict[str, list[dict]] = {m: [] for m in modes}

    # Warmup — first call pays embedding HTTP + reranker model load.
    # Don't include this in stats.
    logger.info("워밍업 (첫 호출의 모델 로드/HTTP 핸드셰이크 비용 제외)...")
    for m in modes:
        try:
            _run_one(rag, m, "소농직불 자격", top_k=top_k)
        except Exception as e:
            logger.warning(f"warmup {m} failed: {e}")

    for q in questions:
        qid = q["id"]
        query = q["query"]
        expected = set(q["expected_clauses"])
        keywords = q.get("expected_keywords", [])

        for mode in modes:
            try:
                cits, latency_ms = _run_one(rag, mode, query, top_k=top_k)
            except Exception as e:
                logger.warning(f"[{qid}/{mode}] 실패: {e}")
                results[mode].append({
                    "id": qid, "query": query, "error": str(e),
                    "latency_ms": None,
                })
                continue

            top1_snippet = cits[0].snippet if cits else ""
            kw_recall = _keyword_recall(top1_snippet, keywords)
            rank = _first_hit_rank(cits, expected)
            row = {
                "id": qid,
                "query": query,
                "category": q.get("category"),
                "type": q.get("type"),
                "difficulty": q.get("difficulty"),
                "expected_clauses": sorted(expected),
                "retrieved": [
                    {
                        "rank": i + 1,
                        "article": c.article,
                        "chapter": c.chapter,
                        "similarity": round(c.similarity, 4),
                        "clause_ids": sorted(_derive_clause_ids(c)),
                        "snippet_head": (c.snippet or "")[:120],
                    }
                    for i, c in enumerate(cits)
                ],
                "hit@1": _hit_at_k(cits, expected, 1),
                "hit@3": _hit_at_k(cits, expected, 3),
                "hit@5": _hit_at_k(cits, expected, 5),
                "first_hit_rank": rank,
                "rr": (1.0 / rank) if rank else 0.0,
                "keyword_recall@1": round(kw_recall, 3),
                "latency_ms": round(latency_ms, 1),
            }
            results[mode].append(row)
            logger.info(
                f"[{qid}/{mode}] hit@1={row['hit@1']} hit@3={row['hit@3']} "
                f"kw={row['keyword_recall@1']:.2f} {row['latency_ms']:.0f}ms — {query[:40]}"
            )

    # ── Aggregate ────────────────────────────────────────────────
    summary: dict[str, dict] = {}
    for mode, rows in results.items():
        ok = [r for r in rows if "error" not in r]
        if not ok:
            summary[mode] = {"n": 0}
            continue
        latencies = [r["latency_ms"] for r in ok if r["latency_ms"] is not None]
        summary[mode] = {
            "n": len(ok),
            "errors": len(rows) - len(ok),
            "hit@1": round(sum(r["hit@1"] for r in ok) / len(ok), 3),
            "hit@3": round(sum(r["hit@3"] for r in ok) / len(ok), 3),
            "hit@5": round(sum(r["hit@5"] for r in ok) / len(ok), 3),
            "mrr": round(sum(r["rr"] for r in ok) / len(ok), 3),
            "kw_recall@1_mean": round(
                sum(r["keyword_recall@1"] for r in ok) / len(ok), 3
            ),
            "latency_ms_p50": round(_percentile(latencies, 0.5), 1),
            "latency_ms_p95": round(_percentile(latencies, 0.95), 1),
            "latency_ms_mean": round(statistics.mean(latencies), 1),
        }

        # Per-difficulty breakdown
        for diff in ("easy", "medium", "hard"):
            sub = [r for r in ok if r.get("difficulty") == diff]
            if sub:
                summary[mode][f"hit@3_{diff}"] = round(
                    sum(r["hit@3"] for r in sub) / len(sub), 3
                )

        # Per-type breakdown
        for typ in ("factual", "casual_synonym", "multi_aspect", "clause_lookup"):
            sub = [r for r in ok if r.get("type") == typ]
            if sub:
                summary[mode][f"hit@3_{typ}"] = round(
                    sum(r["hit@3"] for r in sub) / len(sub), 3
                )

    return {
        "meta": {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "indexed_chunks": rag.count(),
            "top_k": top_k,
            "modes": modes,
            "n_questions": len(questions),
        },
        "summary": summary,
        "results": results,
    }


def render_markdown(report: dict) -> str:
    meta = report["meta"]
    summary = report["summary"]
    lines = [
        f"# Subsidy RAG Eval — {meta['timestamp']}",
        "",
        f"- Indexed chunks: **{meta['indexed_chunks']}**",
        f"- Questions: **{meta['n_questions']}**",
        f"- top_k: **{meta['top_k']}**",
        f"- Modes: **{', '.join(meta['modes'])}**",
        "",
        "## Summary",
        "",
        "| metric | " + " | ".join(meta["modes"]) + " |",
        "|---|" + "---|" * len(meta["modes"]),
    ]
    rows = [
        "n", "errors", "hit@1", "hit@3", "hit@5", "mrr",
        "kw_recall@1_mean",
        "latency_ms_p50", "latency_ms_mean", "latency_ms_p95",
        "hit@3_easy", "hit@3_medium", "hit@3_hard",
        "hit@3_factual", "hit@3_casual_synonym",
        "hit@3_multi_aspect", "hit@3_clause_lookup",
    ]
    for k in rows:
        cells = [str(summary.get(m, {}).get(k, "—")) for m in meta["modes"]]
        lines.append(f"| {k} | " + " | ".join(cells) + " |")

    # Per-question table (mode 0)
    primary = meta["modes"][0]
    lines += ["", f"## Per-question ({primary})", ""]
    lines.append("| id | difficulty | type | hit@1 | hit@3 | rank | kw | ms | query |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for r in report["results"][primary]:
        if "error" in r:
            lines.append(f"| {r['id']} | — | — | ERR | — | — | — | — | {r['query']} |")
            continue
        lines.append(
            f"| {r['id']} | {r['difficulty']} | {r['type']} "
            f"| {'✓' if r['hit@1'] else '✗'} | {'✓' if r['hit@3'] else '✗'} "
            f"| {r['first_hit_rank'] or '—'} | {r['keyword_recall@1']} "
            f"| {r['latency_ms']} | {r['query']} |"
        )

    # Failures detail
    fails = [r for r in report["results"][primary]
             if "error" not in r and not r["hit@3"]]
    if fails:
        lines += ["", f"## Failures (hit@3 miss, {primary})", ""]
        for r in fails:
            lines.append(f"### {r['id']} — {r['query']}")
            lines.append(f"- expected: `{r['expected_clauses']}`")
            lines.append(f"- retrieved top-3:")
            for h in r["retrieved"][:3]:
                lines.append(
                    f"  - `{h['clause_ids']}` sim={h['similarity']} "
                    f"`{h['article']}` — {h['snippet_head']}…"
                )
            lines.append("")

    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--eval-set", default="tests/subsidy_eval/eval_set.json")
    ap.add_argument("--mode", choices=["full", "fast", "both"], default="both")
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--out", default="reports")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    repo_root = Path(__file__).resolve().parent.parent  # .../backend
    eval_path = repo_root / args.eval_set
    if not eval_path.exists():
        logger.error(f"eval set not found: {eval_path}")
        sys.exit(2)

    eval_set = json.loads(eval_path.read_text(encoding="utf-8"))
    logger.info(f"eval set 로드: {len(eval_set['questions'])}개 질문")

    modes = ["full", "fast"] if args.mode == "both" else [args.mode]
    report = evaluate(eval_set, modes=modes, top_k=args.top_k)

    out_dir = repo_root / args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = out_dir / f"subsidy_rag_eval_{stamp}.json"
    md_path = out_dir / f"subsidy_rag_eval_{stamp}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")

    print("\n" + render_markdown(report).split("## Per-question")[0])
    print(f"\nFull report: {md_path}")
    print(f"Raw JSON:    {json_path}")


if __name__ == "__main__":
    main()
