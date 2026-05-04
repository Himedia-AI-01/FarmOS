"""Integration test for the ReasoningBank distillation chain (iter 1–3 + 4).

Exercises the full pipeline without LLM calls or API stack:

    eval-failure (synthesised)
      → STRATEGY_CANDIDATES.md       (writer from eval_farm_agent.py)
      → distill --dry-run             (parser + offline stub emitter)
      → STRATEGY_PROPOSALS.md         (header + run-marker + proposal blocks)

    + iter-4 guardrail helper sanity checks
    + iter-1 memory-source resolution sanity check

Why a self-contained script and not pytest:
    The project ships no pytest dependency; matches the existing
    `scripts/eval_farm_agent.py` pattern (runnable script, stdlib only).

Usage:
    cd backend
    .venv/Scripts/python scripts/test_distill_loop.py
    # exit 0 on success, non-zero + traceback on failure

Run this whenever any of these change:
    - eval_farm_agent.py:_append_strategy_candidate / _failed_check_names
    - distill_strategies.py:parse_candidates / distill / _CANDIDATE_RE
    - farm_agent.py:_is_subsidy_domain_answer / _extract_citations
    - farm_agent/agent.py:_resolve_memory_sources
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

# Project root on sys.path so `app.*` and `scripts.*` resolve when invoked
# directly (no installed package).
_BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND))


class DistillChainTest(unittest.TestCase):
    """End-to-end: synthesise candidates, run distill, verify proposals shape."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="ralph-distill-"))

    # ── iter 2: candidate writer schema ────────────────────────────────────
    def test_failed_check_names_filters_relevant_evaluators(self) -> None:
        from scripts.eval_farm_agent import _failed_check_names
        scores = {
            "must_contain": 0,         # failed → keep
            "citation_present": 1,     # passed → drop
            "latency_ok": 0,           # failed → keep
            "latency_sec": 12.3,       # not in relevant set → drop
            "errored": 0,              # not in relevant set → drop
        }
        self.assertEqual(
            sorted(_failed_check_names(scores)),
            ["latency_ok", "must_contain"],
        )

    def test_candidate_writer_round_trip(self) -> None:
        # Re-target the writer at a temp file so we don't pollute backend/memory.
        import scripts.eval_farm_agent as efa
        original = efa._CANDIDATES_FILE
        cand_path = self.tmp / "STRATEGY_CANDIDATES.md"
        efa._CANDIDATES_FILE = cand_path
        try:
            efa._append_strategy_candidate(
                ex={"input": "직불금 의무?",
                    "tags": ["subsidy", "obligation", "yes_no"]},
                result={"output": "네 필수입니다",
                        "elapsed_sec": 8.3,
                        "errored": False},
                scores={"must_contain": 0,
                        "citation_present": 0,
                        "latency_ok": 1,
                        "verdict_correct": 1},
                path_label="agent",
            )
        finally:
            efa._CANDIDATES_FILE = original

        text = cand_path.read_text(encoding="utf-8")
        self.assertIn("Strategy Candidates", text)              # header written
        self.assertIn("subsidy,obligation,yes_no", text)         # tags
        self.assertIn("must_contain", text)                      # failure mode
        self.assertIn("citation_present", text)
        self.assertIn("observed_path=`agent`", text)

    def test_writer_is_noop_on_clean_pass(self) -> None:
        """Writer must NOT pollute the queue when every check passed."""
        import scripts.eval_farm_agent as efa
        original = efa._CANDIDATES_FILE
        cand_path = self.tmp / "CLEAN_QUEUE.md"
        efa._CANDIDATES_FILE = cand_path
        try:
            efa._append_strategy_candidate(
                ex={"input": "ok", "tags": ["t"]},
                result={"output": "good", "elapsed_sec": 1.0, "errored": False},
                scores={"must_contain": 1, "citation_present": 1, "latency_ok": 1},
                path_label="agent",
            )
        finally:
            efa._CANDIDATES_FILE = original
        self.assertFalse(cand_path.exists(),
                         "no-op path must not even create the queue file")

    # ── iter 3: distill parser + dry-run emitter ───────────────────────────
    def test_distill_parser_extracts_each_block(self) -> None:
        from scripts.distill_strategies import parse_candidates
        synthetic = (
            "header\n---\n"
            "## ⏳ 2026-05-04 12:00 — subsidy,obligation\n"
            "- **When**: pattern A\n"
            "- **Strategy**: stub\n"
            "- **Pitfall**: must_contain failed\n\n"
            "## ⏳ 2026-05-04 12:01 — diagnosis\n"
            "- **When**: pattern B\n"
            "- **Strategy**: stub\n"
            "- **Pitfall**: citation_present failed\n"
        )
        out = parse_candidates(synthetic)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["tags"], "subsidy,obligation")
        self.assertEqual(out[1]["tags"], "diagnosis")
        self.assertIn("pattern A", out[0]["body"])
        self.assertIn("pattern B", out[1]["body"])

    def test_distill_dry_run_writes_well_formed_proposals(self) -> None:
        from scripts.distill_strategies import distill
        in_path = self.tmp / "candidates.md"
        out_path = self.tmp / "proposals.md"
        in_path.write_text(
            "## ⏳ 2026-05-04 12:00 — subsidy\n"
            "- **When**: 사용자 질문 패턴 X\n"
            "- **Strategy**: stub\n"
            "- **Pitfall**: must_contain\n",
            encoding="utf-8",
        )
        n = distill(in_path=in_path, out_path=out_path,
                    limit=None, dry_run=True)
        self.assertEqual(n, 1)
        text = out_path.read_text(encoding="utf-8")
        self.assertIn("Strategy Proposals", text)        # header on first run
        self.assertIn("R-prop.", text)                    # proposal marker
        self.assertIn("distill run @", text)              # run marker comment
        # Append-only: a second run must add a second block, not rewrite.
        n2 = distill(in_path=in_path, out_path=out_path,
                     limit=None, dry_run=True)
        self.assertEqual(n2, 1)
        text2 = out_path.read_text(encoding="utf-8")
        self.assertEqual(text2.count("R-prop."), 2)

    def test_distill_no_input_is_safe(self) -> None:
        from scripts.distill_strategies import distill
        n = distill(in_path=self.tmp / "missing.md",
                    out_path=self.tmp / "proposals.md",
                    limit=None, dry_run=True)
        self.assertEqual(n, 0)

    # ── iter 9: analyze (deterministic failure-mode histogram) ─────────────
    def test_analyze_aggregates_failed_checks(self) -> None:
        from scripts.distill_strategies import analyze
        in_path = self.tmp / "candidates.md"
        in_path.write_text(
            "## ⏳ 2026-05-04 12:00 — subsidy,obligation\n"
            "- **Pitfall**: 실패한 체크 — `must_contain, citation_present`. obs=…\n\n"
            "## ⏳ 2026-05-04 12:01 — subsidy\n"
            "- **Pitfall**: 실패한 체크 — `citation_present`. obs=…\n\n"
            "## ⏳ 2026-05-04 12:02 — diagnosis,pesticide\n"
            "- **Pitfall**: 실패한 체크 — `must_contain, latency_ok`. obs=…\n",
            encoding="utf-8",
        )
        s = analyze(in_path)
        self.assertEqual(s["total"], 3)
        # citation_present appears in 2 of 3 candidates
        self.assertEqual(s["failed_checks"]["citation_present"], 2)
        self.assertEqual(s["failed_checks"]["must_contain"], 2)
        self.assertEqual(s["failed_checks"]["latency_ok"], 1)
        # subsidy tag is in 2 candidates
        self.assertEqual(s["tags"]["subsidy"], 2)
        # tag x check cross-product
        self.assertEqual(s["tag_x_check"]["subsidy::citation_present"], 2)
        self.assertEqual(s["tag_x_check"]["diagnosis::latency_ok"], 1)

    def test_analyze_missing_input_returns_zero_total(self) -> None:
        from scripts.distill_strategies import analyze
        s = analyze(self.tmp / "missing.md")
        self.assertEqual(s["total"], 0)
        self.assertEqual(s["failed_checks"], {})


class BriefingSanitizerTest(unittest.TestCase):
    """iter 16: pin `_strip_meta_reasoning` so a regression can't ship English
    chain-of-thought preambles to Korean farmers in the morning briefing.

    The sanitizer trims any meta-reasoning text before the first markdown
    heading (or briefing-emoji line). Regressions here are visible to every
    user every morning, so the contract must be locked in.
    """

    def test_already_clean_text_passes_through(self) -> None:
        from app.services.farm_agent.briefing import _strip_meta_reasoning
        clean = "## 🌅 좋은 아침입니다\n- 오늘 날씨 맑음"
        self.assertEqual(_strip_meta_reasoning(clean), clean)

    def test_strips_english_meta_reasoning_before_heading(self) -> None:
        from app.services.farm_agent.briefing import _strip_meta_reasoning
        text = ("Let's start by thinking about what to include.\n"
                "We have weather data and pest data approximate to the user.\n\n"
                "## 🌅 사장님, 좋은 아침입니다\n- 오늘 맑음")
        out = _strip_meta_reasoning(text)
        self.assertTrue(out.startswith("## 🌅"),
                        f"sanitizer must drop preamble, got: {out!r}")
        self.assertNotIn("Let's", out)
        self.assertNotIn("approximate", out)

    def test_empty_input_returns_empty(self) -> None:
        from app.services.farm_agent.briefing import _strip_meta_reasoning
        self.assertEqual(_strip_meta_reasoning(""), "")

    def test_plain_text_without_markers_unchanged(self) -> None:
        from app.services.farm_agent.briefing import _strip_meta_reasoning
        # No markdown heading, no meta markers → caller will handle as-is.
        plain = "오늘은 정말 좋은 날입니다. 농사 잘 되세요."
        self.assertEqual(_strip_meta_reasoning(plain), plain)

    def test_strips_when_emoji_only_start(self) -> None:
        from app.services.farm_agent.briefing import _strip_meta_reasoning
        text = ("Let's draft the briefing now.\n"
                "🌅 사장님, 좋은 아침입니다\n- 오늘 맑음")
        out = _strip_meta_reasoning(text)
        # Emoji line should become the start (or near-start) of the output.
        self.assertIn("🌅 사장님", out.splitlines()[0])
        self.assertNotIn("Let's", out)


class FastPathSafetyTest(unittest.TestCase):
    """iter 14: pin fast_path safety so a regex edit can't bypass safety review.

    `try_fast_path` returns templated tool output WITHOUT LLM oversight. A
    safety-sensitive query (농약, 직불, 진단…) accidentally matching a fast
    pattern would produce confidently-wrong answers about money and pesticides.
    These tests fence the BLOCKLIST and the length cap.
    """

    SAFETY_QUERIES = [
        # Pesticide / pest diagnosis — must never fast-path.
        "농약 추천해줘",
        "고추 탄저병 진단",
        "벼 도열병 방제 약 알려줘",
        "응애 농약 희석배수",
        "병해충 살포 시기",
        # Subsidy / eligibility — must never fast-path.
        "직불금 받을 수 있어?",
        "보조금 자격 알려줘",
        "공익직불 시행지침 어디서 봐",
        "지원금 신청 자격",
    ]

    BENIGN_QUERIES = [
        "오늘 날씨 어때?",
        "내일 비 와?",
        "벼 시세 얼마야",
        "어제 환기 했어?",
        "오늘 영농일지 보여줘",
    ]

    def test_blocklist_rejects_safety_queries(self) -> None:
        from app.services.farm_agent.fast_path import _BLOCKLIST
        for q in self.SAFETY_QUERIES:
            with self.subTest(q=q):
                self.assertIsNotNone(
                    _BLOCKLIST.search(q),
                    f"BLOCKLIST must match safety-sensitive query: {q!r}",
                )

    def test_blocklist_does_not_reject_benign_queries(self) -> None:
        from app.services.farm_agent.fast_path import _BLOCKLIST
        for q in self.BENIGN_QUERIES:
            with self.subTest(q=q):
                self.assertIsNone(
                    _BLOCKLIST.search(q),
                    f"BLOCKLIST must NOT match benign fast-path query: {q!r}",
                )

    def test_max_len_cap_blocks_long_inputs(self) -> None:
        # Even a benign-looking query that exceeds the length cap must be
        # rejected (fast_path uses settings.FARM_AGENT_FAST_PATH_MAX_LEN). We
        # don't import settings here — instead exercise try_fast_path directly
        # with a string longer than any reasonable cap.
        import asyncio
        from app.services.farm_agent.fast_path import try_fast_path
        very_long = "오늘 날씨 어때? " * 20  # 200+ chars
        out = asyncio.run(try_fast_path(very_long, user_id="test-user"))
        self.assertIsNone(out, "fast_path must reject inputs over the length cap")

    def test_empty_input_is_rejected(self) -> None:
        import asyncio
        from app.services.farm_agent.fast_path import try_fast_path
        self.assertIsNone(asyncio.run(try_fast_path("", "u")))
        self.assertIsNone(asyncio.run(try_fast_path("   ", "u")))


class JsonSummaryTest(unittest.TestCase):
    """iter 13: build_json_summary shape pinned for CI regression gating."""

    def _scores(self, **overrides: int) -> dict:
        # Default: every relevant check passes. Override individual keys to fail.
        base = {"must_contain": 1, "must_not_contain": 1,
                "citation_present": 1, "latency_ok": 1,
                "verdict_correct": 1}
        base.update(overrides)
        return base

    def test_all_pass_summary(self) -> None:
        from scripts.eval_farm_agent import build_json_summary
        cases = [
            {"input": "Q1", "expected": {}, "tags": ["subsidy"]},
            {"input": "Q2", "expected": {}, "tags": ["weather"]},
        ]
        paired = [
            ({"output": "ok", "elapsed_sec": 1.0,
              "errored": False, "fast_path": False}, self._scores()),
            ({"output": "ok", "elapsed_sec": 0.4,
              "errored": False, "fast_path": True}, self._scores()),
        ]
        s = build_json_summary(cases, paired)
        self.assertEqual(s["version"], 1)
        self.assertEqual(s["total_examples"], 2)
        self.assertEqual(s["total_pass"], s["total_checks"])
        self.assertEqual(s["pass_ratio"], 1.0)
        self.assertTrue(s["all_passed"])
        self.assertEqual(len(s["examples"]), 2)
        self.assertEqual(s["examples"][1]["fast_path"], True)
        self.assertTrue(s["examples"][0]["all_passed"])

    def test_partial_failure_summary(self) -> None:
        from scripts.eval_farm_agent import build_json_summary
        cases = [{"input": "Q", "expected": {}, "tags": ["subsidy"]}]
        paired = [(
            {"output": "bad", "elapsed_sec": 8.0,
             "errored": False, "fast_path": False},
            self._scores(citation_present=0, must_contain=0),
        )]
        s = build_json_summary(cases, paired)
        self.assertFalse(s["all_passed"])
        self.assertLess(s["pass_ratio"], 1.0)
        ex = s["examples"][0]
        self.assertEqual(ex["scores"]["citation_present"], 0)
        self.assertFalse(ex["all_passed"])

    def test_empty_dataset_summary_is_safe(self) -> None:
        from scripts.eval_farm_agent import build_json_summary
        s = build_json_summary([], [])
        self.assertEqual(s["total_examples"], 0)
        self.assertEqual(s["pass_ratio"], 0.0)
        self.assertFalse(s["all_passed"])  # vacuous-truth guard


class DatasetFilterTest(unittest.TestCase):
    """iter 12: --example / --list filters for the eval CLI."""

    DATASET = [
        {"input": "Q1", "expected": {}, "tags": ["subsidy", "obligation"]},
        {"input": "Q2", "expected": {}, "tags": ["weather", "fast_path"]},
        {"input": "Q3", "expected": {}, "tags": ["subsidy", "eligibility"]},
    ]

    def test_passthrough_when_no_selector(self) -> None:
        from scripts.eval_farm_agent import filter_dataset
        self.assertEqual(len(filter_dataset(self.DATASET, None)), 3)
        self.assertEqual(len(filter_dataset(self.DATASET, "")), 3)

    def test_integer_index_selector(self) -> None:
        from scripts.eval_farm_agent import filter_dataset
        out = filter_dataset(self.DATASET, "1")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["input"], "Q2")

    def test_tag_selector_matches_multiple(self) -> None:
        from scripts.eval_farm_agent import filter_dataset
        out = filter_dataset(self.DATASET, "subsidy")
        self.assertEqual(len(out), 2)
        self.assertEqual({ex["input"] for ex in out}, {"Q1", "Q3"})

    def test_index_out_of_range_raises(self) -> None:
        from scripts.eval_farm_agent import filter_dataset
        with self.assertRaises(ValueError):
            filter_dataset(self.DATASET, "99")

    def test_unknown_tag_raises(self) -> None:
        from scripts.eval_farm_agent import filter_dataset
        with self.assertRaises(ValueError):
            filter_dataset(self.DATASET, "no_such_tag")


class VerdictCalibrationTest(unittest.TestCase):
    """iter 10/11: subsidy-agent must hedge on verdict_hint=UNCLEAR.

    These tests pin both halves of the calibration loop:
      - the prompt actually carries the UNCLEAR rule (regression detector for
        accidental prompt edits)
      - the evaluator scores all three verdict branches correctly so a real
        eval run will catch a behaviour regression.
    """

    def test_mandatory_branch(self) -> None:
        from scripts.eval_farm_agent import eval_obligation_verdict
        # affirm + no deny → pass
        s = eval_obligation_verdict("필수입니다 [doc > 제5조]",
                                     {"verdict": "MANDATORY"})
        self.assertEqual(s["verdict_correct"], 1)
        # deny phrase present → fail
        s = eval_obligation_verdict("필수가 아닙니다",
                                     {"verdict": "MANDATORY"})
        self.assertEqual(s["verdict_correct"], 0)

    def test_optional_branch(self) -> None:
        from scripts.eval_farm_agent import eval_obligation_verdict
        s = eval_obligation_verdict("의무는 아닙니다, 선택사항입니다",
                                     {"verdict": "OPTIONAL"})
        self.assertEqual(s["verdict_correct"], 1)
        # confident MANDATORY assertion when expected OPTIONAL → fail
        s = eval_obligation_verdict("필수입니다 반드시 해야 합니다",
                                     {"verdict": "OPTIONAL"})
        self.assertEqual(s["verdict_correct"], 0)

    def test_unclear_branch_hedges(self) -> None:
        from scripts.eval_farm_agent import eval_obligation_verdict
        hedged = ("⚠️ 시행지침상 명확한 의무 조항을 찾지 못했습니다. "
                  "직불 담당 (읍면동) 에 확인을 권장합니다.")
        s = eval_obligation_verdict(hedged, {"verdict": "UNCLEAR"})
        self.assertEqual(s["verdict_correct"], 1)

    def test_unclear_branch_rejects_confident_answer(self) -> None:
        from scripts.eval_farm_agent import eval_obligation_verdict
        # Confidently asserts MANDATORY when verdict is UNCLEAR — must fail.
        s = eval_obligation_verdict("필수입니다 반드시 해야 합니다",
                                     {"verdict": "UNCLEAR"})
        self.assertEqual(s["verdict_correct"], 0)

    def test_subsidy_prompt_carries_unclear_rule(self) -> None:
        # Regression detector: the iter-10 prompt rule must remain in place.
        from app.services.farm_agent.prompts import SUBSIDY_PROMPT
        self.assertIn("UNCLEAR", SUBSIDY_PROMPT)
        self.assertIn("담당", SUBSIDY_PROMPT)
        self.assertIn("⚠", SUBSIDY_PROMPT)


class GuardrailHelpersTest(unittest.TestCase):
    """iter 4: domain detection + citation extraction."""

    def test_subsidy_question_uncited_answer(self) -> None:
        from app.api.farm_agent import _is_subsidy_domain_answer, _extract_citations
        self.assertTrue(_is_subsidy_domain_answer("직불금 의무?", "네 필수입니다"))
        self.assertEqual(_extract_citations("네 필수입니다"), [])

    def test_subsidy_question_cited_answer(self) -> None:
        from app.api.farm_agent import _extract_citations
        cites = _extract_citations("필수입니다 [공익직불 시행지침 > 제12조]")
        self.assertEqual(len(cites), 1)
        self.assertIn("제12조", cites[0]["label"])

    def test_weather_out_of_domain(self) -> None:
        from app.api.farm_agent import _is_subsidy_domain_answer
        self.assertFalse(_is_subsidy_domain_answer("내일 비?", "흐림"))


class MemorySourcesTest(unittest.TestCase):
    """iter 1: AGENTS.md + STRATEGIES.md both resolved into MemoryMiddleware."""

    def test_resolve_includes_both_default_files(self) -> None:
        from app.services.farm_agent.agent import _resolve_memory_sources
        sources = _resolve_memory_sources()
        self.assertTrue(any("AGENTS.md" in s for s in sources),
                        f"AGENTS.md missing from {sources}")
        self.assertTrue(any("STRATEGIES.md" in s for s in sources),
                        f"STRATEGIES.md missing from {sources}")


class VerifierCandidatesTest(unittest.TestCase):
    """iter 19: verifier FAIL/UNKNOWN runtime queue writer."""

    def setUp(self) -> None:
        from app.services.farm_agent import verifier_candidates as vc
        self._vc = vc
        self._orig_path = vc.CANDIDATES_FILE
        self._tmpdir = tempfile.TemporaryDirectory()
        vc.CANDIDATES_FILE = Path(self._tmpdir.name) / "DIAGNOSIS_CANDIDATES.md"

    def tearDown(self) -> None:
        self._vc.CANDIDATES_FILE = self._orig_path
        self._tmpdir.cleanup()

    def test_parse_verdict_recognises_three_branches(self) -> None:
        self.assertEqual(self._vc.parse_verdict("PASS"), "PASS")
        self.assertEqual(self._vc.parse_verdict("FAIL\n불일치 항목: 농약명"), "FAIL")
        self.assertEqual(self._vc.parse_verdict("UNKNOWN\n사유: 타임아웃"), "UNKNOWN")
        self.assertEqual(self._vc.parse_verdict("  pass  "), "PASS")
        self.assertIsNone(self._vc.parse_verdict(""))
        # PASS appearing mid-sentence must NOT match — anchor at start only.
        self.assertIsNone(self._vc.parse_verdict("일반 답변에 PASS 단어가 등장"))

    def test_pass_does_not_write(self) -> None:
        result = self._vc.record_verifier_verdict(
            "PASS", question="고추탄저병?", user_id="u1", session_id="s1"
        )
        self.assertIsNone(result)
        self.assertFalse(self._vc.CANDIDATES_FILE.exists())

    def test_fail_writes_entry(self) -> None:
        result = self._vc.record_verifier_verdict(
            "FAIL\n불일치 항목: 농약명\n원본: 만코제브",
            question="고추 탄저병 약?", user_id="u1", session_id="s1",
        )
        self.assertEqual(result, "FAIL")
        self.assertTrue(self._vc.CANDIDATES_FILE.exists())
        body = self._vc.CANDIDATES_FILE.read_text(encoding="utf-8")
        self.assertIn("verifier:fail", body)
        self.assertIn("고추 탄저병 약?", body)
        self.assertIn("불일치 항목", body)
        self.assertIn("user=u1", body)

    def test_unknown_writes_entry(self) -> None:
        result = self._vc.record_verifier_verdict(
            "UNKNOWN\n사유: diagnose_pest 타임아웃",
            question="병해 식별", user_id="u2", session_id="s2",
        )
        self.assertEqual(result, "UNKNOWN")
        body = self._vc.CANDIDATES_FILE.read_text(encoding="utf-8")
        self.assertIn("verifier:unknown", body)
        self.assertIn("타임아웃", body)

    def test_header_written_once_then_appended(self) -> None:
        self._vc.record_verifier_verdict("FAIL\nfoo", question="q1")
        self._vc.record_verifier_verdict("UNKNOWN\nbar", question="q2")
        body = self._vc.CANDIDATES_FILE.read_text(encoding="utf-8")
        self.assertEqual(body.count("# Diagnosis Candidates"), 1)
        self.assertIn("verifier:fail", body)
        self.assertIn("verifier:unknown", body)

    def test_unparseable_content_is_noop(self) -> None:
        # Non-verifier subagent output (subsidy / farm-data / diagnosis answer)
        # must not trigger a write — they don't emit PASS/FAIL/UNKNOWN prefixes.
        result = self._vc.record_verifier_verdict(
            "직불금은 필수입니다 [시행지침 > 제12조]",
            question="직불금 의무?",
        )
        self.assertIsNone(result)
        self.assertFalse(self._vc.CANDIDATES_FILE.exists())


class VerifierPromptContractTest(unittest.TestCase):
    """iter 21: pin VERIFIER_PROMPT's three-verdict output contract.

    The iter-19 runtime hook (`verifier_candidates.record_verifier_verdict`)
    only fires when the verifier subagent emits content whose first token is
    `PASS|FAIL|UNKNOWN`. If a future prompt edit drops one of those tokens
    or changes the format, the queue silently stops capturing — no error,
    no noise, just an invisible regression in the failure-mining pipeline.
    These tests make the contract explicit.
    """

    def test_prompt_mandates_three_verdict_tokens(self) -> None:
        from app.services.farm_agent.prompts import VERIFIER_PROMPT
        for token in ("PASS", "FAIL", "UNKNOWN"):
            self.assertIn(token, VERIFIER_PROMPT,
                          f"VERIFIER_PROMPT must mandate `{token}` — iter-19 hook depends on it")

    def test_prompt_three_verdicts_match_runtime_parser(self) -> None:
        # Tighter coupling check: the parser's regex must accept exactly the
        # three tokens the prompt mandates, no more / no less.
        from app.services.farm_agent.verifier_candidates import parse_verdict
        for token in ("PASS", "FAIL", "UNKNOWN"):
            self.assertEqual(parse_verdict(token), token)
        # And a token the prompt does NOT mandate must not match.
        self.assertIsNone(parse_verdict("MAYBE\n사유: ..."))
        self.assertIsNone(parse_verdict("ERROR\n사유: ..."))

    def test_prompt_keeps_string_match_safety_clause(self) -> None:
        # Verifier MUST NOT paraphrase — that's the safety property the whole
        # subagent exists to enforce. If the prompt loses this clause, the
        # verifier becomes a rubber stamp.
        from app.services.farm_agent.prompts import VERIFIER_PROMPT
        self.assertIn("문자열", VERIFIER_PROMPT)  # 문자열 일치 / 문자열 단위
        self.assertIn("의역", VERIFIER_PROMPT)    # 의역·축약 금지 clause


class DiagnosisAnalyzerTest(unittest.TestCase):
    """iter 20: --analyze-diagnosis aggregator over verifier candidate queue."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="ralph-diag-analyze-"))

    def _seed(self, n_fail: int, n_unknown: int) -> Path:
        """Drive the iter-19 writer to produce n_fail FAIL + n_unknown UNKNOWN entries."""
        from app.services.farm_agent import verifier_candidates as vc
        orig = vc.CANDIDATES_FILE
        try:
            vc.CANDIDATES_FILE = self.tmp / "DIAGNOSIS_CANDIDATES.md"
            for i in range(n_fail):
                vc.record_verifier_verdict(
                    "FAIL\n불일치 항목: 농약명",
                    question=f"고추 탄저병 약 {i}", user_id="u", session_id="s",
                )
            for i in range(n_unknown):
                vc.record_verifier_verdict(
                    "UNKNOWN\n사유: 타임아웃",
                    question=f"진단 실패 케이스 {i}", user_id="u", session_id="s",
                )
            return vc.CANDIDATES_FILE
        finally:
            vc.CANDIDATES_FILE = orig

    def test_analyze_diagnosis_counts_verdicts(self) -> None:
        from scripts.distill_strategies import analyze_diagnosis
        path = self._seed(n_fail=3, n_unknown=2)
        s = analyze_diagnosis(path)
        self.assertEqual(s["total"], 5)
        self.assertEqual(s["verdicts"].get("FAIL"), 3)
        self.assertEqual(s["verdicts"].get("UNKNOWN"), 2)
        # Tag passthrough — iter-19 writer tags are `verifier:fail|unknown`.
        self.assertEqual(s["tags"].get("verifier:fail"), 3)
        self.assertEqual(s["tags"].get("verifier:unknown"), 2)

    def test_analyze_diagnosis_buckets_question_prefixes(self) -> None:
        from scripts.distill_strategies import analyze_diagnosis
        path = self._seed(n_fail=2, n_unknown=0)
        s = analyze_diagnosis(path)
        # Both FAIL questions begin with "고추 탄저병 약" — within the 30-char
        # prefix cap they must collapse onto one bucket.
        prefixes = s["question_prefixes"]
        self.assertTrue(any("고추 탄저병 약" in k for k in prefixes),
                        f"question prefix bucket missing: {prefixes}")
        self.assertEqual(sum(prefixes.values()), 2)

    def test_analyze_diagnosis_missing_input_is_zero_total(self) -> None:
        from scripts.distill_strategies import analyze_diagnosis
        s = analyze_diagnosis(self.tmp / "does-not-exist.md")
        self.assertEqual(s["total"], 0)
        self.assertEqual(s["verdicts"], {})
        self.assertEqual(s["question_prefixes"], {})


class LangChainOpenAIWarningsTest(unittest.TestCase):
    """iter 27: ChatOpenAI invocation must not emit parameter-placement warnings."""

    def test_build_llm_does_not_warn_about_parallel_tool_calls(self) -> None:
        # langchain_openai 0.3+ emits a UserWarning when `parallel_tool_calls`
        # is passed as a top-level kwarg ("transferred to model_kwargs"). We
        # now place it directly in model_kwargs — this test pins that and
        # catches a regression where someone bumps the kwarg back up.
        import warnings, importlib
        from app.services.farm_agent import agent as agent_mod
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            importlib.reload(agent_mod)
            agent_mod._build_llm()
        offenders = [w for w in caught if "parallel_tool_calls" in str(w.message)]
        self.assertFalse(
            offenders,
            f"_build_llm emitted parallel_tool_calls warning(s): "
            f"{[str(w.message) for w in offenders]}",
        )


class LangGraphV1MigrationTest(unittest.TestCase):
    """iter 26: verifier graph must use the v1.0+ create_agent path."""

    def test_verifier_graph_does_not_emit_v10_deprecation(self) -> None:
        # `langgraph.prebuilt.create_react_agent` is deprecated in LangGraph
        # v1.0 and slated for removal in v2.0. This test catches a regression
        # where someone reverts the import.
        import warnings, importlib
        from app.services.farm_agent import verifier_graph
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            importlib.reload(verifier_graph)
        offenders = [
            w for w in caught
            if "LangGraphDeprecatedSinceV10" in str(type(w.category).__name__) + str(w.message)
            or "create_react_agent has been moved" in str(w.message)
        ]
        self.assertFalse(offenders,
                         f"verifier_graph reload triggered v1.0 deprecation: {offenders}")

    def test_verifier_graph_compiles_to_state_graph(self) -> None:
        from app.services.farm_agent import verifier_graph
        # Whatever the import path, the result must still be a compiled graph
        # that AsyncSubAgent can dispatch to.
        self.assertEqual(verifier_graph.graph.__class__.__name__, "CompiledStateGraph")


class LangGraphManifestTest(unittest.TestCase):
    """iter 25: langgraph.json must stay aligned with pyproject + CI Python."""

    def test_python_version_matches_pyproject(self) -> None:
        import json, re
        manifest = json.loads((_BACKEND / "langgraph.json").read_text(encoding="utf-8"))
        pyproject_text = (_BACKEND / "pyproject.toml").read_text(encoding="utf-8")
        m = re.search(r'requires-python\s*=\s*">=\s*(\d+\.\d+)"', pyproject_text)
        self.assertIsNotNone(m, "pyproject.toml must declare requires-python = \">=X.Y\"")
        required_minor = m.group(1)
        # langgraph.json pins one specific minor version for cloud deploys —
        # it must satisfy the pyproject floor. Bumping pyproject without
        # bumping the manifest would silently ship deploys on the old Python.
        self.assertEqual(
            manifest["python_version"], required_minor,
            f"langgraph.json python_version {manifest['python_version']!r} "
            f"does not match pyproject requires-python floor {required_minor!r}",
        )

    def test_verifier_graph_entrypoint_resolves(self) -> None:
        # The iter-19 candidate writer's existence is justified by the verifier
        # subagent emitting PASS/FAIL/UNKNOWN. If langgraph.json drops the
        # verifier-agent entry or repoints it, that whole iter goes silent.
        import json
        manifest = json.loads((_BACKEND / "langgraph.json").read_text(encoding="utf-8"))
        self.assertIn("verifier-agent", manifest["graphs"])
        target = manifest["graphs"]["verifier-agent"]
        self.assertTrue(target.endswith(":graph"),
                        f"verifier-agent target must end with ':graph', got {target!r}")
        # And the file must actually exist + have a `graph` symbol.
        from app.services.farm_agent import verifier_graph
        self.assertTrue(hasattr(verifier_graph, "graph"))


class DiagnosisDistillTest(unittest.TestCase):
    """iter 22: --diagnosis distill mode (paired with iter-19 + iter-20)."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="ralph-diag-distill-"))
        # Seed a real diagnosis-candidates file via the iter-19 writer.
        from app.services.farm_agent import verifier_candidates as vc
        self._vc = vc
        self._orig_path = vc.CANDIDATES_FILE
        vc.CANDIDATES_FILE = self.tmp / "DIAGNOSIS_CANDIDATES.md"
        vc.record_verifier_verdict(
            "FAIL\n불일치 항목: 농약명",
            question="고추 탄저병 약 추천?", user_id="u", session_id="s",
        )
        vc.record_verifier_verdict(
            "UNKNOWN\n사유: diagnose_pest 타임아웃",
            question="감자 잎마름병", user_id="u", session_id="s",
        )

    def tearDown(self) -> None:
        self._vc.CANDIDATES_FILE = self._orig_path

    def test_dry_run_writes_diagnosis_proposals(self) -> None:
        from scripts.distill_strategies import distill
        out = self.tmp / "DIAGNOSIS_PROPOSALS.md"
        n = distill(
            in_path=self.tmp / "DIAGNOSIS_CANDIDATES.md",
            out_path=out, limit=None, dry_run=True, diagnosis=True,
        )
        self.assertEqual(n, 2)
        body = out.read_text(encoding="utf-8")
        # Diagnosis-flavoured header + D-prop. (not R-prop.) stub headings +
        # `Fix:` field (not `Strategy:`). These three together pin the schema
        # — a future regex regression in either prompt or template would fail.
        self.assertIn("Diagnosis Proposals", body)
        self.assertIn("D-prop.", body)
        self.assertNotIn("R-prop.", body)
        self.assertIn("**Fix**", body)
        self.assertIn("flavour=diagnosis", body)

    def test_strategy_mode_unchanged_after_refactor(self) -> None:
        # Regression guard for iter-3 strategy distill: the iter-22 refactor
        # added a `diagnosis=` kwarg with a False default. Calling the original
        # signature on a strategy-flavoured input must still emit R-prop. stubs.
        from scripts.distill_strategies import distill
        from scripts.eval_farm_agent import _append_strategy_candidate

        # Drive iter-2 writer to seed a strategy candidate.
        import scripts.eval_farm_agent as ev
        orig = ev._CANDIDATES_FILE
        try:
            ev._CANDIDATES_FILE = self.tmp / "STRATEGY_CANDIDATES.md"
            _append_strategy_candidate(
                ex={"input": "직불금 의무?", "tags": ["subsidy"]},
                result={"output": "확인 못 함", "elapsed_sec": 1.2},
                scores={"errored": 0, "must_contain": 0, "citation_present": 0, "latency_ok": 1},
                path_label="probe",
            )
            out = self.tmp / "STRATEGY_PROPOSALS.md"
            n = distill(
                in_path=ev._CANDIDATES_FILE,
                out_path=out, limit=None, dry_run=True,  # default diagnosis=False
            )
        finally:
            ev._CANDIDATES_FILE = orig
        self.assertEqual(n, 1)
        body = out.read_text(encoding="utf-8")
        self.assertIn("R-prop.", body)
        self.assertNotIn("D-prop.", body)
        self.assertIn("**Strategy**", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
