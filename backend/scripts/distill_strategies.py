"""Distill STRATEGY_CANDIDATES.md → STRATEGY_PROPOSALS.md.

ReasoningBank-style write-back loop, **gated**:

  eval failures
    → STRATEGY_CANDIDATES.md  (raw, example-specific drafts; written by
                                eval_farm_agent.py)
    → distill_strategies.py   (this script — generalises drafts into
                                rule-shaped entries via LLM)
    → STRATEGY_PROPOSALS.md   (LLM output, awaiting human review)
    → human reviewer copy-edits and promotes into STRATEGIES.md, which the
      Deep Agent loads via MemoryMiddleware.

We never auto-promote into STRATEGIES.md. The eval set is small and an
unattended loop would pollute the strategy memory with overfitted entries.

Usage:
    cd backend
    uv run python scripts/distill_strategies.py            # LLM mode
    uv run python scripts/distill_strategies.py --dry-run  # template only,
                                                           # no LLM call
    uv run python scripts/distill_strategies.py --limit 3  # top 3 drafts
    uv run python scripts/distill_strategies.py \\
        --in memory/STRATEGY_CANDIDATES.md \\
        --out memory/STRATEGY_PROPOSALS.md

Source:
    Google ReasoningBank (arXiv 2509.25140) §3 — distil generalisable
    reasoning strategies from self-judged successful and failed experiences.
    The "active failure analysis" claim is what we operationalise here.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import logging
import re
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")


_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_IN = _BACKEND_ROOT / "memory" / "STRATEGY_CANDIDATES.md"
_DEFAULT_OUT = _BACKEND_ROOT / "memory" / "STRATEGY_PROPOSALS.md"
# iter 22 — diagnosis-distillation defaults (paired with iter-19 writer +
# iter-20 analyzer). Same parser, different curator prompt and reviewer file.
_DEFAULT_DIAGNOSIS_OUT = _BACKEND_ROOT / "memory" / "DIAGNOSIS_PROPOSALS.md"


_PROPOSALS_HEADER = """# Strategy Proposals — Human Review Queue

LLM-generalised drafts from `STRATEGY_CANDIDATES.md`. Each block below is a
*proposal* — the reviewer must:

1. Verify the **When** clause is general (not bound to one example's wording).
2. Verify the **Strategy** is actionable (names a tool / subagent / decision rule).
3. Verify the **Pitfall** describes the failure mode this rule prevents.
4. If accepted, move the entry into `STRATEGIES.md` (which is loaded into every
   LLM call) and delete from this file.

LLM output is **never** automatically promoted. Source: ReasoningBank
(arXiv 2509.25140).

---
"""


# Match the candidate-entry block written by eval_farm_agent.py.
# Each entry begins with `## ⏳ {ts} — {tags}` and continues until the next
# `## ` heading or end-of-file.
_CANDIDATE_RE = re.compile(
    r"^##\s+⏳\s+(?P<ts>[^\n—]+?)\s+—\s+(?P<tags>[^\n]*)\n(?P<body>(?:(?!^## )[\s\S])*)",
    re.MULTILINE,
)


def parse_candidates(text: str) -> list[dict[str, str]]:
    """Extract candidate blocks. Returns [{ts, tags, body}, ...] in file order."""
    out: list[dict[str, str]] = []
    for m in _CANDIDATE_RE.finditer(text):
        out.append({
            "ts": m.group("ts").strip(),
            "tags": m.group("tags").strip(),
            "body": m.group("body").rstrip(),
        })
    return out


# Recognise the failed-check token list inside a candidate body. Matches the
# format that `eval_farm_agent.py:_append_strategy_candidate` writes:
#   "실패한 체크 — `must_contain, citation_present`."
_FAILED_LIST_RE = re.compile(r"실패한 체크\s*[—\-:]\s*`([^`]+)`")


def _candidate_failed_checks(body: str) -> list[str]:
    """Pull the failed-evaluator names out of a candidate's body. [] if absent."""
    m = _FAILED_LIST_RE.search(body)
    if not m:
        return []
    return [x.strip() for x in m.group(1).split(",") if x.strip()]


def analyze(in_path: Path) -> dict[str, Any]:
    """Aggregate the candidate queue into a deterministic failure-mode histogram.

    No LLM. Useful for reviewers who need to prioritise distillation work — the
    most-common (failed_check, tag) pairs are the highest-leverage targets.
    Also the building block for future selective-replay retrieval (matching a
    new query's predicted failure mode against the catalog).
    """
    summary: dict[str, Any] = {
        "input": str(in_path),
        "total": 0,
        "failed_checks": {},   # {check_name: count}
        "tags": {},            # {tag: count}
        "tag_x_check": {},     # {"tag::check": count}
    }
    if not in_path.exists():
        return summary
    raw = in_path.read_text(encoding="utf-8")
    cands = parse_candidates(raw)
    summary["total"] = len(cands)
    for c in cands:
        checks = _candidate_failed_checks(c["body"])
        tags = [t for t in (c.get("tags") or "").split(",") if t.strip()]
        for k in checks:
            summary["failed_checks"][k] = summary["failed_checks"].get(k, 0) + 1
        for t in tags:
            summary["tags"][t] = summary["tags"].get(t, 0) + 1
        for t in tags:
            for k in checks:
                key = f"{t.strip()}::{k}"
                summary["tag_x_check"][key] = summary["tag_x_check"].get(key, 0) + 1
    return summary


def _print_analysis(summary: dict[str, Any]) -> None:
    """Render the histogram as a reviewer-friendly table on stdout."""
    print(f"\nCandidate queue: {summary['input']}")
    print(f"Total candidates: {summary['total']}")
    if not summary["total"]:
        return

    def _table(title: str, rows: dict[str, int]) -> None:
        if not rows:
            return
        print(f"\n  {title}")
        for k, v in sorted(rows.items(), key=lambda kv: (-kv[1], kv[0])):
            print(f"    {v:>4}  {k}")

    _table("Failed checks (most-failed first):", summary["failed_checks"])
    _table("Tags:", summary["tags"])
    _table("Tag × failed check (top targets):", summary["tag_x_check"])


# Diagnosis-candidate analyzer (iter 20). Reuses parse_candidates() because
# verifier_candidates.py writes the same `## ⏳ ts — tags` skeleton; only the
# body differs (verdict text instead of failed-check list).
_DEFAULT_DIAGNOSIS_IN = _BACKEND_ROOT / "memory" / "DIAGNOSIS_CANDIDATES.md"

# Recognise the question excerpt the runtime hook embeds in the body, e.g.:
#   "- **When**: 진단 검증이 `FAIL` 로 끝났을 때 — `고추 탄저병 약?`"
_DIAGNOSIS_QUESTION_RE = re.compile(
    r"진단 검증이\s*`(?P<verdict>FAIL|UNKNOWN)`\s*로 끝났을 때\s*[—\-]\s*`(?P<question>[^`]+)`"
)


def _diagnosis_extract(body: str) -> tuple[str | None, str | None]:
    """Pull (verdict, question_excerpt) from a diagnosis candidate body."""
    m = _DIAGNOSIS_QUESTION_RE.search(body)
    if not m:
        return None, None
    return m.group("verdict"), m.group("question").strip()


def analyze_diagnosis(in_path: Path) -> dict[str, Any]:
    """Aggregate the diagnosis-verifier queue.

    Buckets:
      - verdicts: {FAIL|UNKNOWN: count} — which failure mode dominates?
      - question_prefixes: {first-30-chars: count} — recurring queries?
      - tags: {raw tag string: count} — passthrough for completeness.
    """
    summary: dict[str, Any] = {
        "input": str(in_path),
        "total": 0,
        "verdicts": {},
        "question_prefixes": {},
        "tags": {},
    }
    if not in_path.exists():
        return summary
    raw = in_path.read_text(encoding="utf-8")
    cands = parse_candidates(raw)
    summary["total"] = len(cands)
    for c in cands:
        verdict, question = _diagnosis_extract(c["body"])
        tag_raw = (c.get("tags") or "").strip()
        if tag_raw:
            summary["tags"][tag_raw] = summary["tags"].get(tag_raw, 0) + 1
        if verdict:
            summary["verdicts"][verdict] = summary["verdicts"].get(verdict, 0) + 1
        if question:
            # 30 chars is enough to disambiguate similar queries without
            # blowing up the histogram into one row per unique sentence.
            prefix = question[:30]
            summary["question_prefixes"][prefix] = (
                summary["question_prefixes"].get(prefix, 0) + 1
            )
    return summary


def _print_diagnosis_analysis(summary: dict[str, Any]) -> None:
    print(f"\nDiagnosis queue: {summary['input']}")
    print(f"Total verdicts: {summary['total']}")
    if not summary["total"]:
        return

    def _table(title: str, rows: dict[str, int]) -> None:
        if not rows:
            return
        print(f"\n  {title}")
        for k, v in sorted(rows.items(), key=lambda kv: (-kv[1], kv[0])):
            print(f"    {v:>4}  {k}")

    _table("Verdicts:", summary["verdicts"])
    _table("Tags (raw):", summary["tags"])
    _table("Question prefixes (top recurring queries):", summary["question_prefixes"])


_DISTILL_SYSTEM = """\
당신은 한국 농업 AI 에이전트(FarmOS Deep Agent)의 reasoning-strategy 큐레이터다.

입력은 평가에서 실패한 사용자 질문 한 건의 raw 후보 항목이다.
당신의 임무: 이 한 건을 **일반화 가능한** 전략 규칙으로 다시 쓰는 것.

출력 형식 (정확히 이 마크다운 구조, 한국어):

## R-prop. {짧고 일반적인 제목}
- **When**: {특정 예시 단어가 아닌, 패턴/조건으로 기술}
- **Strategy**: {어떤 도구·서브에이전트·인용 규칙·검증 단계를 적용해야 실패가 사라지는가}
- **Pitfall**: {이 규칙이 막는 실패 모드 - 1-2문장}

규칙:
- 입력의 사용자 문장을 그대로 인용하지 말 것 — 패턴으로 추상화
- 도메인 상수(작물명·약품명·연도)는 빼고 카테고리 단어로
- "When/Strategy/Pitfall" 헤더 글자수 그대로 사용
- 한 후보당 정확히 한 항목만 출력
"""


_DIAGNOSIS_DISTILL_SYSTEM = """\
당신은 한국 농업 AI 에이전트의 진단-검증(verifier) 실패 분석 큐레이터다.

입력은 verifier 서브에이전트가 진단 답변에 대해 FAIL 또는 UNKNOWN 판정을 내린
한 건의 raw 후보 항목이다 (질문 + verifier 출력 텍스트 포함).

당신의 임무: 이 한 건을 **일반화 가능한** 진단-도구 개선 제안으로 다시 쓰는 것.
strategy-curator 와 달리, 당신의 출력은 *에이전트 reasoning 규칙* 이 아니라
*도구·라우팅·프롬프트* 변경 제안이다 (예: "diagnose_pest 가 X 작물에서 농약명을
누락 → 데이터 소스 보강", "verifier 가 농약 제조사 변형을 FAIL 처리 → 동의어 사전").

출력 형식 (정확히 이 마크다운 구조, 한국어):

## D-prop. {짧고 일반적인 제목}
- **When**: {verifier 가 어떤 패턴의 답변/도구출력에서 실패했는가}
- **Fix**: {진단 도구 / 라우팅 / verifier 프롬프트 중 어느 것을 어떻게 바꿔야 하는가}
- **Pitfall**: {이 수정이 막는 실패 모드 - 1-2문장}

규칙:
- 사용자 질문 그대로 인용 금지 — 패턴으로 추상화
- 작물명·약품명·지역명은 카테고리 단어로
- 한 후보당 정확히 한 항목만 출력
"""


def _llm_distill_one(
    candidate: dict[str, str],
    system_prompt: str = _DISTILL_SYSTEM,
) -> str | None:
    """Call the project's configured LLM to generalise one candidate.

    Returns the LLM's markdown block or None on failure (caller falls back to
    template). Best-effort: never raises.
    """
    try:
        from langchain_core.messages import HumanMessage, SystemMessage
        from app.services.farm_agent.agent import _build_llm
    except Exception as exc:  # noqa: BLE001
        logger.warning("distill.llm_import_failed err=%s — falling back to template", exc)
        return None

    try:
        llm = _build_llm()
        user = (
            f"Tags: {candidate['tags']}\n"
            f"Captured: {candidate['ts']}\n\n"
            f"Raw candidate:\n{candidate['body']}\n"
        )
        resp = llm.invoke([SystemMessage(system_prompt), HumanMessage(user)])
        text = (getattr(resp, "content", "") or "").strip()
        if not text:
            return None
        return text
    except Exception as exc:  # noqa: BLE001
        logger.warning("distill.llm_call_failed err=%s", exc)
        return None


def _template_distill_one(
    candidate: dict[str, str],
    *,
    diagnosis: bool = False,
) -> str:
    """Offline fallback — emit a clearly-marked stub that needs reviewer work.

    `diagnosis=True` switches the stub schema from `R-prop.` (strategy) to
    `D-prop.` (diagnosis-tool fix proposal) so a reviewer can tell at a glance
    which queue this came from.
    """
    if diagnosis:
        return (
            f"## D-prop. (stub) {candidate['tags'] or 'untagged'}\n"
            f"- **When**: _(reviewer: which verifier-failure pattern does "
            f"this represent?)_\n"
            f"- **Fix**: _(reviewer: which diagnosis tool / routing / "
            f"verifier-prompt change?)_\n"
            f"- **Pitfall**: _(reviewer: 1-2 sentences naming the failure mode)_\n"
            f"- **Raw**: ts={candidate['ts']}; tags={candidate['tags']}\n"
        )
    return (
        f"## R-prop. (stub) {candidate['tags'] or 'untagged'}\n"
        f"- **When**: _(reviewer: generalise the user pattern from the raw "
        f"candidate body)_\n"
        f"- **Strategy**: _(reviewer: which tool / subagent / verification "
        f"step prevents this failure?)_\n"
        f"- **Pitfall**: _(reviewer: 1-2 sentences naming the failure mode)_\n"
        f"- **Raw**: ts={candidate['ts']}; tags={candidate['tags']}\n"
    )


_DIAGNOSIS_PROPOSALS_HEADER = """# Diagnosis Proposals — Human Review Queue

LLM-generalised drafts from `DIAGNOSIS_CANDIDATES.md` (verifier FAIL/UNKNOWN
queue). Each block below is a *proposal* for a diagnosis-tool, routing, or
verifier-prompt change. Reviewer must:

1. Verify the **When** clause is general (not bound to one verifier output).
2. Verify the **Fix** is actionable (names a tool / route / prompt edit).
3. Verify the **Pitfall** describes the failure mode this fix prevents.
4. If accepted, file the change as a code edit; do **not** auto-merge.

LLM output is **never** automatically promoted. Source: ReasoningBank
(arXiv 2509.25140) — failure-mining pipeline applied to runtime verifier
disagreements rather than offline eval failures.

---
"""


def distill(
    in_path: Path,
    out_path: Path,
    limit: int | None,
    dry_run: bool,
    *,
    diagnosis: bool = False,
) -> int:
    """Returns the count of proposals written. 0 on no-op.

    `diagnosis=True` swaps the curator system prompt and proposals header to
    the diagnosis-tool flavour (paired with iter-19 verifier candidate writer).
    Parser is shared — both queues use the same `## ⏳ ts — tags` skeleton.
    """
    if not in_path.exists():
        logger.info("distill.no_input path=%s — nothing to do", in_path)
        return 0

    raw = in_path.read_text(encoding="utf-8")
    candidates = parse_candidates(raw)
    if not candidates:
        logger.info("distill.no_candidates parsed=0 — input may be empty/malformed")
        return 0

    if limit:
        candidates = candidates[:limit]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not out_path.exists():
        header = _DIAGNOSIS_PROPOSALS_HEADER if diagnosis else _PROPOSALS_HEADER
        out_path.write_text(header, encoding="utf-8")

    system_prompt = _DIAGNOSIS_DISTILL_SYSTEM if diagnosis else _DISTILL_SYSTEM
    run_ts = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    written = 0
    flavour = "diagnosis" if diagnosis else "strategy"
    with out_path.open("a", encoding="utf-8") as f:
        f.write(f"\n<!-- distill run @ {run_ts} · {len(candidates)} candidate(s) "
                f"· mode={'dry-run' if dry_run else 'llm'} · flavour={flavour} -->\n")
        for cand in candidates:
            block = None if dry_run else _llm_distill_one(cand, system_prompt)
            if block is None:
                block = _template_distill_one(cand, diagnosis=diagnosis)
            f.write("\n" + block.rstrip() + "\n")
            written += 1

    logger.info(
        "distill.done in=%s out=%s written=%d mode=%s flavour=%s",
        in_path, out_path, written, "dry-run" if dry_run else "llm", flavour,
    )
    return written


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--in", dest="in_path", default=str(_DEFAULT_IN),
                   help=f"input candidates file (default: {_DEFAULT_IN.relative_to(_BACKEND_ROOT)})")
    p.add_argument("--out", dest="out_path", default=str(_DEFAULT_OUT),
                   help=f"output proposals file (default: {_DEFAULT_OUT.relative_to(_BACKEND_ROOT)})")
    p.add_argument("--limit", type=int, default=None,
                   help="distill only the first N candidates (default: all)")
    p.add_argument("--dry-run", action="store_true",
                   help="skip LLM call, emit stub proposals only — useful for CI")
    p.add_argument("--analyze", action="store_true",
                   help="print failure-mode histogram from the candidate queue and exit "
                        "(no distillation, no LLM)")
    p.add_argument("--analyze-diagnosis", action="store_true",
                   help="print verdict + question histogram from the verifier-agent "
                        f"diagnosis queue (default path: "
                        f"{_DEFAULT_DIAGNOSIS_IN.relative_to(_BACKEND_ROOT)}) and exit")
    p.add_argument("--diagnosis", action="store_true",
                   help="distill the verifier diagnosis queue instead of the strategy "
                        "queue. Swaps default --in/--out paths to the DIAGNOSIS_* "
                        "files and uses the diagnosis-tool curator prompt.")
    args = p.parse_args()

    if args.analyze:
        summary = analyze(Path(args.in_path))
        _print_analysis(summary)
        sys.exit(0)

    if args.analyze_diagnosis:
        # If --in was overridden, honour it; otherwise default to the
        # diagnosis file (iter 19 writer's destination).
        if args.in_path == str(_DEFAULT_IN):
            target = _DEFAULT_DIAGNOSIS_IN
        else:
            target = Path(args.in_path)
        summary = analyze_diagnosis(target)
        _print_diagnosis_analysis(summary)
        sys.exit(0)

    # iter 22 — when --diagnosis is set and the user did not override the
    # paths, swap to the diagnosis defaults. An explicit --in / --out wins.
    in_path = Path(args.in_path)
    out_path = Path(args.out_path)
    if args.diagnosis:
        if args.in_path == str(_DEFAULT_IN):
            in_path = _DEFAULT_DIAGNOSIS_IN
        if args.out_path == str(_DEFAULT_OUT):
            out_path = _DEFAULT_DIAGNOSIS_OUT

    n = distill(
        in_path=in_path,
        out_path=out_path,
        limit=args.limit,
        dry_run=args.dry_run,
        diagnosis=args.diagnosis,
    )
    print(f"distill: {n} proposal(s) appended to {out_path}")
    sys.exit(0 if n >= 0 else 1)


if __name__ == "__main__":
    main()
