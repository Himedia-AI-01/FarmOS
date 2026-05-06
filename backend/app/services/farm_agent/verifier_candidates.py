"""Verifier-agent FAIL/UNKNOWN → diagnosis-candidate queue.

Mirror of `eval_farm_agent.py`'s strategy-candidate writer (iter 2), but for
*runtime* verifier verdicts rather than offline eval failures.

The verifier subagent's prompt (prompts.VERIFIER_PROMPT) mandates that its
answer start with one of three tokens:
  - `PASS`                         → diagnosis verified, nothing to learn
  - `FAIL\\n불일치 항목: ...`        → mismatch detected, mining target
  - `UNKNOWN\\n사유: ...`           → tool failure / timeout, mining target

We append `FAIL` / `UNKNOWN` cases to `backend/memory/DIAGNOSIS_CANDIDATES.md`
so a human reviewer can spot recurring diagnostic-tool failure modes (e.g. a
specific pest+crop combo where the verifier consistently flags inconsistency).

Why a separate file from STRATEGY_CANDIDATES.md:
  Strategy candidates are about *agent reasoning* failures from the eval
  harness. Diagnosis candidates are about *runtime tool* disagreements caught
  by the safety verifier. Different review cadence; different reviewer.

ReasoningBank §3 calls this "active analysis of failed experiences to source
counterfactual signals" (arXiv 2509.25140). Per iter 2's contract, this
writer NEVER auto-merges into STRATEGIES.md — proposals only.
"""

from __future__ import annotations

import datetime as _dt
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# `parents[3]` mirrors agent.py's `_BACKEND_ROOT` — points at `backend/`.
_BACKEND_ROOT = Path(__file__).resolve().parents[3]
CANDIDATES_FILE = _BACKEND_ROOT / "memory" / "DIAGNOSIS_CANDIDATES.md"

CANDIDATES_HEADER = """# Diagnosis Candidates — Verifier FAIL/UNKNOWN Queue

Auto-appended by the verifier-agent post-stream hook on every FAIL or UNKNOWN
verdict. Reviewer queue — **never** auto-merged into STRATEGIES.md or used by
the agent at runtime. Schema mirrors STRATEGY_CANDIDATES.md so the same
distill_strategies.py pipeline can curate these.

Source: ReasoningBank (arXiv 2509.25140 §3) — failure mining for
counterfactual signal extraction.
"""

# Verifier prompt mandates the verdict be the first token. Match leniently
# for surrounding whitespace / BOM but anchor at start so a `PASS` mention
# inside a longer answer can't trigger a false UNKNOWN write.
_VERDICT_RE = re.compile(r"^\s*(PASS|FAIL|UNKNOWN)\b", re.IGNORECASE)


def parse_verdict(content: str) -> str | None:
    """Return `'PASS' | 'FAIL' | 'UNKNOWN'` or None if no verdict prefix."""
    if not content:
        return None
    m = _VERDICT_RE.match(content)
    return m.group(1).upper() if m else None


def record_verifier_verdict(
    content: str,
    *,
    question: str,
    user_id: str | int = "",
    session_id: str = "",
) -> str | None:
    """Append a FAIL/UNKNOWN candidate; return the verdict that was written.

    Returns None on PASS (nothing to learn), unparseable content (not from
    the verifier subagent), or any I/O error (best-effort, never raises).
    """
    verdict = parse_verdict(content)
    if verdict in (None, "PASS"):
        return None
    try:
        CANDIDATES_FILE.parent.mkdir(parents=True, exist_ok=True)
        if not CANDIDATES_FILE.exists():
            CANDIDATES_FILE.write_text(CANDIDATES_HEADER, encoding="utf-8")
        ts = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        # Trim aggressively — the candidate file is for human scanning, not
        # archival. Reviewers can re-run the diagnosis if they need full text.
        q_excerpt = (question or "(empty)").replace("\n", " ")[:200]
        v_excerpt = content.replace("\n", " ")[:400]
        entry = (
            f"\n## ⏳ {ts} — verifier:{verdict.lower()}\n"
            f"- **When**: 진단 검증이 `{verdict}` 로 끝났을 때 — `{q_excerpt}`\n"
            f"- **Strategy**: _(reviewer: 어떤 도구·프롬프트·라우팅 변화로 이 불일치를 줄일 수 있는가?)_\n"
            f"- **Pitfall**: verifier 출력 — \"{v_excerpt}\"\n"
            f"- **Source**: verifier-agent runtime hook user={user_id} session={session_id} @ {ts}\n"
        )
        with CANDIDATES_FILE.open("a", encoding="utf-8") as f:
            f.write(entry)
        return verdict
    except Exception as exc:  # noqa: BLE001 — never let writer break the stream
        logger.warning("verifier_candidates.append_failed err=%s", exc)
        return None
