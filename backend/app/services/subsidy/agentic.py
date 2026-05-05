"""Agentic RAG layer for the subsidy pipeline.

Sits between the user query and the Redis hybrid retriever. Adds:

  P0  CRAG (Corrective RAG) verifier
        After retrieval, Haiku rates top-k leaves on 0/1/2 relevance.
        - all 0  → reformulate query, retry once. If still all 0 → return [].
        - mixed  → drop the 0s, keep ≥1.
        Skipped when retriever is confident (top-1 vs top-3 score gap > threshold).

  P1  Query planner
        Single Haiku call before retrieval. Outputs:
          intent       : factual | clause_lookup | multi_aspect | compare | off_topic
          sub_queries  : list[str]   (for multi_aspect; else len 1)
          rewritten    : HyDE-style formal rewrite of the user query
          clause_id    : "II-3" / "C2-VI" / None for direct clause lookups
        Replaces static QUERY_SYNONYMS by handling open vocabulary via LLM rewrite.

  P2  Recursive cross-reference expansion
        시행지침 has internal references: "참고 14", "별표 4 카목", "법 제19조".
        After retrieval, scan top-k leaves' text for these patterns, fetch the
        referenced leaves from Redis, append them to context. Max depth = 1.

  P3  Adaptive routing
        Heuristic over top-3 score gap decides which of P0/P2 actually run.
          high gap (>SUBSIDY_CRAG_CONFIDENT_GAP)
            → keep top-3, skip CRAG, still run recursive refs.
          low gap or all low scores
            → expand to top-10, run CRAG, run recursive refs.

Why one module:
    Each capability is a leaf-level helper. Composing them in one file keeps
    the agentic pipeline auditable as a single unit. ``run_agentic_search``
    is the public entry — it dispatches all four phases.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

import httpx
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

from app.core.config import settings

logger = logging.getLogger(__name__)


# ── Lazy LLM client (shared between planner + CRAG) ──────────────────────


_llm_singleton: ChatOpenAI | None = None


def _get_agentic_llm() -> ChatOpenAI | None:
    """OpenRouter Haiku 4.5 client. None if OPENROUTER_API_KEY is missing."""
    global _llm_singleton
    if _llm_singleton is not None:
        return _llm_singleton
    if not settings.OPENROUTER_API_KEY:
        return None
    http_client = httpx.Client(
        http1=True, http2=False, timeout=httpx.Timeout(15.0, connect=5.0),
    )
    _llm_singleton = ChatOpenAI(
        model=settings.SUBSIDY_AGENTIC_LLM_MODEL,
        base_url=settings.OPENROUTER_BASE_URL,
        api_key=settings.OPENROUTER_API_KEY,
        temperature=0.0,
        max_tokens=400,
        http_client=http_client,
        default_headers={
            "HTTP-Referer": "https://github.com/FarmOS-v2",
            "X-Title": "FarmOS Subsidy Agentic RAG",
        },
    )
    return _llm_singleton


# ── P1: Query planner ────────────────────────────────────────────────────


@dataclass
class QueryPlan:
    intent: str               # factual | clause_lookup | multi_aspect | compare | off_topic
    sub_queries: list[str] = field(default_factory=list)
    rewritten: str = ""
    clause_id: str | None = None
    raw: str = ""             # Raw LLM output for debug

    @property
    def is_off_topic(self) -> bool:
        return self.intent == "off_topic"


_PLANNER_PROMPT = """당신은 한국 농림축산식품부 공익직불사업 시행지침 RAG 시스템의 질의 분석기입니다.

사용자의 질문을 분석하여 검색 전략을 결정하세요.

# 도메인 anchor (검색 대상 문서)
2026년도 기본형 공익직불사업 시행지침 — 공익직불제 운영 매뉴얼.
- 지원금: 소농직불금 (130만원 정액), 면적직불금 (구간별 단가).
- 8대 준수사항: 농업경영체 등록, 영농기록 작성, 농약·화학비료, 영농폐기물,
  교육 이수, 마을공동체, 농지 형상유지, 가축분뇨.
- 부정수급 처분: 환수, 5배 추가징수, 신청제한 (1·3·5·8년).
- 농업인 유형: 일반·청년·후계·전업·2030세대·신규·귀농.

# 출력 (JSON ONLY)
{
  "intent": "factual" | "clause_lookup" | "multi_aspect" | "compare" | "off_topic",
  "sub_queries": ["...", "..."],
  "rewritten": "공식 시행지침 용어로 다시 쓴 질문 (1문장, 50자 이내)",
  "clause_id": "II-3" | "C2-VI" | "II-8" | null
}

# intent 분류 규칙
- clause_lookup: "II-3 알려줘", "지급단가 조항", "II-8" 등 조항 식별자가 명시되거나
  "지급단가/소농직불 조항이 어디" 같이 직접 문서 위치를 묻는 경우.
  → clause_id 채움. sub_queries 는 빈 리스트.
- multi_aspect: 두 개 이상의 독립된 주제가 한 질문에 — "자격이랑 처벌", "농약+비료".
  → sub_queries 에 각 주제별 검색어 (2~4개) 작성.
- compare: 두 개념의 차이/비교 — "신규 vs 기존수혜자", "소농 vs 면적".
  → sub_queries 에 각 항목 1개씩.
- off_topic: 시행지침과 무관 — 날씨, 시세, 음식 등.
  → sub_queries 빈 리스트, rewritten 빈 문자열.
- factual: 위 어디에도 해당 안 됨, 단일 사실 질의.
  → sub_queries 에 rewritten 한 개만.

# rewritten 작성
- 사용자가 캐주얼/구어체로 적은 질문을 시행지침 본문에 등장하는 공식 용어로 변환.
  예: "영농일지 안 쓰면 어떻게 돼?" → "영농기록 작성·보관 의무 위반시 행정처분"
      "토해내야 해?" → "환수 행정처분 대상"
      "자격이 뭐야?" → "지급대상 자격요건"
- 의미만 옮기고 새로운 사실을 만들지 마세요.

# 예시
사용자: "영농일지 안 쓰면 어떻게 돼?"
{"intent":"factual","sub_queries":["영농기록 작성·보관 의무 위반시 행정처분"],"rewritten":"영농기록 작성·보관 의무 위반시 행정처분","clause_id":null}

사용자: "II-8 알려줘"
{"intent":"clause_lookup","sub_queries":[],"rewritten":"","clause_id":"II-8"}

사용자: "소농직불 자격이랑 부정수급 처벌 알려줘"
{"intent":"multi_aspect","sub_queries":["소농직불 지급대상 자격요건","부정수급 행정처분 환수"],"rewritten":"소농직불 자격요건 및 부정수급 행정처분","clause_id":null}

사용자: "오늘 날씨 어때?"
{"intent":"off_topic","sub_queries":[],"rewritten":"","clause_id":null}

# 사용자 질문
"""


async def plan_query(query: str) -> QueryPlan:
    """Run the Haiku planner. Falls back to factual+raw_query on error."""
    if not settings.SUBSIDY_PLANNER_ENABLED:
        return QueryPlan(intent="factual", sub_queries=[query], rewritten=query)

    llm = _get_agentic_llm()
    if llm is None:
        return QueryPlan(intent="factual", sub_queries=[query], rewritten=query)

    prompt = _PLANNER_PROMPT + query.strip() + "\n\nJSON:"
    try:
        # ChatOpenAI exposes a native async invoke — using to_thread spins a
        # worker thread per call for no benefit and inflates p95 latency.
        msg = await llm.ainvoke([HumanMessage(content=prompt)])
        text = msg.content if isinstance(msg.content, str) else ""
    except Exception as e:  # noqa: BLE001 — planner failures must not break retrieval
        logger.warning("agentic.planner.llm_failed err=%s", e)
        return QueryPlan(intent="factual", sub_queries=[query], rewritten=query, raw="<error>")

    plan = _parse_plan(text, fallback_query=query)
    logger.info(
        "agentic.planner intent=%s subq=%d clause=%s rewritten=%r",
        plan.intent, len(plan.sub_queries), plan.clause_id, plan.rewritten[:60],
    )
    return plan


def _normalize_clause_id(raw: str) -> str:
    """Map planner-emitted clause ids to canonical chunker form.

    Planner sometimes returns '별표4' or '별표 4' instead of 'BP4'.
    Other variants from observed outputs:
      'II-3' / 'C2-VI'         → unchanged (canonical)
      '별표4' / '별표 4' / '별표.4' → 'BP4'
      'BP4' / 'bp4'            → 'BP4'
    """
    s = raw.strip().replace(" ", "").replace(".", "")
    if not s:
        return ""
    # 별표 N → BP<N>
    m = re.match(r"^별표(\d{1,2})$", s)
    if m:
        return f"BP{m.group(1)}"
    # bp<N> → BP<N>
    m = re.match(r"^[Bb][Pp](\d{1,2})$", s)
    if m:
        return f"BP{m.group(1)}"
    # Already canonical (II-3, C2-VI, II, III, etc.)
    return s


def _parse_plan(text: str, fallback_query: str) -> QueryPlan:
    """Best-effort JSON extraction from planner output."""
    # LLM may wrap in code fences or add commentary — find the JSON block.
    m = re.search(r"\{[^{}]*\}", text, re.DOTALL)
    if not m:
        return QueryPlan(intent="factual", sub_queries=[fallback_query],
                         rewritten=fallback_query, raw=text)
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return QueryPlan(intent="factual", sub_queries=[fallback_query],
                         rewritten=fallback_query, raw=text)

    intent = str(data.get("intent", "factual")).lower()
    if intent not in {"factual", "clause_lookup", "multi_aspect", "compare", "off_topic"}:
        intent = "factual"

    sub_qs = data.get("sub_queries") or []
    if not isinstance(sub_qs, list):
        sub_qs = []
    sub_qs = [s for s in (str(x).strip() for x in sub_qs) if s]

    rewritten = str(data.get("rewritten") or "").strip()
    clause_id = data.get("clause_id")
    if clause_id is not None:
        clause_id = _normalize_clause_id(str(clause_id).strip()) or None

    # Defaults: factual queries should have at least one sub_query
    if intent in ("factual",) and not sub_qs:
        sub_qs = [rewritten or fallback_query]

    return QueryPlan(
        intent=intent,
        sub_queries=sub_qs,
        rewritten=rewritten or fallback_query,
        clause_id=clause_id,
        raw=text,
    )


# ── P0: CRAG verifier ────────────────────────────────────────────────────


_CRAG_PROMPT = """당신은 한국 농림축산식품부 공익직불사업 시행지침 RAG 의 관련성 평가자입니다.

사용자 질문에 대해 검색된 각 후보 문서가 얼마나 직접적으로 답을 담고 있는지 평가하세요.

# 평가 등급
- 2 : 직접 답이 있음 — 이 문서만으로 답 가능, 핵심 사실/조항 포함.
- 1 : 관련성 있음 — 답을 보완·확장할 수 있는 정보. 단독으로는 부족.
- 0 : 무관 — 키워드는 일치할 수 있으나 질문과 다른 주제.

# 출력 (JSON 배열만)
[{"id": "...", "score": 2}, {"id": "...", "score": 0}, ...]

# 사용자 질문
{query}

# 후보 문서들
{candidates}

JSON:"""


async def crag_verify(
    query: str, leaves: list[dict[str, Any]], *, top_n: int = 5,
) -> list[dict[str, Any]]:
    """Score top-n leaves; return those with score ≥ 1, ordered by score then rank.

    Returns empty list if all are 0 (signals caller to reformulate or give up).
    Falls through to original ranking on LLM error.
    """
    if not leaves or not settings.SUBSIDY_CRAG_ENABLED:
        return leaves

    llm = _get_agentic_llm()
    if llm is None:
        return leaves

    candidates = leaves[:top_n]
    cand_text = "\n\n".join(
        f"[id={i}] clause={c.get('clause_id', '')}  text:\n{(c.get('text') or '')[:600]}"
        for i, c in enumerate(candidates)
    )
    # Cap user-controlled query so a multi-KB paste cannot blow Haiku's context
    # window; also strip placeholder tokens so a query containing literal
    # "{candidates}" cannot corrupt the second .replace below.
    safe_query = (query or "")[:500].replace("{candidates}", "{ candidates }").replace("{query}", "{ query }")
    prompt = _CRAG_PROMPT.replace("{query}", safe_query).replace("{candidates}", cand_text)

    try:
        msg = await llm.ainvoke([HumanMessage(content=prompt)])
        text = msg.content if isinstance(msg.content, str) else ""
    except Exception as e:  # noqa: BLE001
        logger.warning("agentic.crag.llm_failed err=%s", e)
        return leaves

    scores = _parse_crag_scores(text, n=len(candidates))
    if scores is None:
        logger.warning("agentic.crag.parse_failed text=%r", text[:80])
        return leaves

    annotated = []
    for i, (cand, sc) in enumerate(zip(candidates, scores, strict=True)):
        cand2 = dict(cand)
        cand2["_crag_score"] = sc
        cand2["_orig_rank"] = i
        annotated.append(cand2)

    # Drop 0s; preserve relative order for ties.
    kept = [c for c in annotated if c["_crag_score"] >= 1]
    kept.sort(key=lambda c: (-c["_crag_score"], c["_orig_rank"]))

    if not kept:
        logger.info("agentic.crag.all_zero — caller should reformulate/give_up")

    # Append remaining un-verified leaves after the verified ones, untouched —
    # if we pull more than top_n later we still have access to the tail.
    tail = leaves[top_n:]
    return kept + tail


def _parse_crag_scores(text: str, n: int) -> list[int] | None:
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        return None
    try:
        arr = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(arr, list):
        return None
    out: list[int] = []
    for item in arr:
        if isinstance(item, dict):
            sc = item.get("score", 0)
        else:
            sc = item
        try:
            sc_int = int(sc)
        except (TypeError, ValueError):
            sc_int = 0
        out.append(max(0, min(2, sc_int)))
    # Pad/truncate to expected length n
    if len(out) < n:
        out.extend([0] * (n - len(out)))
    return out[:n]


# ── P2: Recursive cross-reference expansion ──────────────────────────────


# Patterns observed in 시행지침 leaf text. Conservative — we only follow the
# clear identifier forms, not loose mentions like "위의 조항".
_REF_PARTICIPLE_RE = re.compile(
    r"(?:참고|별표)\s*(\d{1,2})"
)
_REF_LAW_RE = re.compile(
    r"법\s*제(\d{1,3})조(?:제(\d{1,2})항)?"
)


def _extract_refs_from_leaves(leaves: list[dict[str, Any]]) -> list[str]:
    """Pull a set of referenced clause ids from leaf bodies.

    Returns list of clause_ids the recursive expander should fetch:
      "BP4"   from "별표 4"
      "참고14" → not a stable clause_id, skip (chunker doesn't expose 참고 as IDs)
      "법 제19조" → unmapped at present (would need a law→clause table)

    For now we only expand 별표 references — those have clean BP<n> ids.
    """
    seen: set[str] = set()
    out: list[str] = []
    for leaf in leaves:
        text = leaf.get("text", "") or ""
        for m in _REF_PARTICIPLE_RE.finditer(text):
            # Only 별표 produces a clause_id; 참고 N is not registered as one.
            full = m.group(0)
            num = m.group(1)
            if "별표" in full:
                cid = f"BP{num}"
                if cid not in seen:
                    seen.add(cid)
                    out.append(cid)
        # 법 제N조: out of scope for now (no law→clause map). Skip silently.
    return out


async def expand_recursive_refs(
    leaves: list[dict[str, Any]], *, max_refs: int = 3,
) -> list[dict[str, Any]]:
    """Append referenced leaves (from 별표 N) to retrieved set. Idempotent.

    Bounded to max_refs additional clauses to keep context size manageable.
    Skipped when SUBSIDY_RECURSIVE_REFS_ENABLED is False.
    """
    if not leaves or not settings.SUBSIDY_RECURSIVE_REFS_ENABLED:
        return leaves

    refs = _extract_refs_from_leaves(leaves)
    if not refs:
        return leaves

    # Deduplicate against already-retrieved leaves.
    have = {leaf.get("clause_id") for leaf in leaves if leaf.get("clause_id")}
    refs = [r for r in refs if r not in have][:max_refs]
    if not refs:
        return leaves

    from app.services.subsidy.redis_index import get_clause_leaves

    fetched: list[dict[str, Any]] = []
    for cid in refs:
        try:
            extra = await get_clause_leaves(cid, max_chars=1500)
        except Exception as e:  # noqa: BLE001
            logger.warning("agentic.recursive_ref.fetch_failed cid=%s err=%s", cid, e)
            continue
        # Mark for traceability + dedup later
        for L in extra:
            L["_recursive_ref"] = cid
        fetched.extend(extra)

    if fetched:
        logger.info(
            "agentic.recursive_ref.expanded refs=%s added_leaves=%d",
            refs, len(fetched),
        )
    return leaves + fetched


# ── P3: Adaptive routing helpers ─────────────────────────────────────────


def is_confident(leaves: list[dict[str, Any]]) -> bool:
    """Retriever is confident when top-3 leaves agree on clause_id.

    Heuristic chosen because RRF fusion produces tightly-clustered scores
    (range ~0.01-0.05) — absolute or relative gap thresholds don't separate
    confident from uncertain queries reliably.

    Definition of confident:
      - top-1 leaf's clause_id appears at least twice in top-3, OR
      - top-3 leaves are all from the same parent_id (sub-leaves of one section).

    When confident → skip CRAG (saves ~3s per query, ~70% of traffic).
    """
    if len(leaves) < 3:
        return False
    top3 = leaves[:3]
    clauses = [L.get("clause_id") or "" for L in top3]
    parents = [L.get("parent_id") or "" for L in top3]

    top_clause = clauses[0]
    if top_clause and clauses.count(top_clause) >= 2:
        return True
    top_parent = parents[0]
    if top_parent and parents.count(top_parent) >= 3:
        return True
    return False


# ── Top-level orchestrator ───────────────────────────────────────────────


@dataclass
class AgenticResult:
    leaves: list[dict[str, Any]]
    plan: QueryPlan
    used_crag: bool
    used_recursive: bool
    reformulated: bool


async def run_agentic_search(
    query: str, *, top_k: int = 5,
) -> AgenticResult:
    """Full agentic pipeline: plan → retrieve → (CRAG) → recursive_refs.

    Returns the final ranked list of leaf-dicts (not Citations — caller maps
    to Citation), plus diagnostic flags.
    """
    from app.services.subsidy.redis_index import (
        get_clause_leaves,
        hybrid_search,
    )

    # ── P1: plan ──────────────────────────────────────────────
    plan = await plan_query(query)

    # Off-topic: skip retrieval entirely.
    if plan.is_off_topic:
        logger.info("agentic.off_topic q=%r — returning empty", query[:60])
        return AgenticResult(leaves=[], plan=plan, used_crag=False,
                             used_recursive=False, reformulated=False)

    # Clause-direct lookup: planner identified an explicit clause id.
    if plan.intent == "clause_lookup" and plan.clause_id:
        leaves = await get_clause_leaves(plan.clause_id, max_chars=3_000)
        # Recursive refs still apply to clause-direct fetches.
        if settings.SUBSIDY_RECURSIVE_REFS_ENABLED:
            leaves = await expand_recursive_refs(leaves[:top_k])
        return AgenticResult(
            leaves=leaves[:top_k + 3],
            plan=plan, used_crag=False, used_recursive=True, reformulated=False,
        )

    # ── Retrieval (single or multi-aspect) ───────────────────
    queries = plan.sub_queries or [plan.rewritten or query]
    pool_size = max(top_k * 4, 20)

    if len(queries) == 1:
        leaves = await hybrid_search(queries[0], k=pool_size)
    else:
        # Multi-aspect: run sub-queries in parallel, RRF-merge results
        sub_results = await asyncio.gather(*(
            hybrid_search(q, k=pool_size) for q in queries
        ))
        leaves = _rrf_merge(sub_results, k_const=60, top_n=pool_size)

    if not leaves:
        return AgenticResult(leaves=[], plan=plan, used_crag=False,
                             used_recursive=False, reformulated=False)

    # ── P3: adaptive routing — decide whether to run CRAG ────
    confident = is_confident(leaves)
    used_crag = False
    reformulated = False

    if not confident and settings.SUBSIDY_CRAG_ENABLED:
        verified = await crag_verify(query, leaves, top_n=min(8, len(leaves)))
        used_crag = True

        if not verified or not [v for v in verified if v.get("_crag_score", 0) >= 1]:
            # All zero — try one reformulation with the planner's rewritten form
            # as a different angle. If still zero, return empty.
            if plan.rewritten and plan.rewritten != query:
                retry_leaves = await hybrid_search(plan.rewritten, k=pool_size)
                if retry_leaves:
                    verified = await crag_verify(query, retry_leaves, top_n=min(8, len(retry_leaves)))
                    reformulated = True

        leaves = verified

    leaves = leaves[:top_k]

    # ── P2: recursive cross-references ───────────────────────
    used_recursive = False
    if leaves:
        before_n = len(leaves)
        leaves = await expand_recursive_refs(leaves)
        used_recursive = len(leaves) > before_n

    return AgenticResult(
        leaves=leaves, plan=plan,
        used_crag=used_crag, used_recursive=used_recursive,
        reformulated=reformulated,
    )


def _rrf_merge(
    results_lists: list[list[dict[str, Any]]],
    *, k_const: int = 60, top_n: int = 20,
) -> list[dict[str, Any]]:
    """Reciprocal Rank Fusion across multiple ranked lists.

    Used by multi-aspect path to merge sub-query results into one.
    """
    fused: dict[str, dict[str, Any]] = {}
    for rl in results_lists:
        for rank, h in enumerate(rl, start=1):
            key = h.get("id") or h.get("key") or ""
            if not key:
                continue
            if key not in fused:
                d = dict(h)
                d["_rrf"] = 0.0
                fused[key] = d
            fused[key]["_rrf"] += 1.0 / (k_const + rank)
    out = sorted(fused.values(), key=lambda x: x["_rrf"], reverse=True)
    for r in out:
        r["score"] = r.pop("_rrf")
    return out[:top_n]
