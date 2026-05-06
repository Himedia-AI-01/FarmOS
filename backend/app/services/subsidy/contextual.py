"""Anthropic Contextual Retrieval — 청크별 LLM 생성 맥락 prefix.

배경 (Anthropic, 2024): 표준 RAG 는 청크를 그 자체로만 임베딩한다. 그래서
"농업인은 영농기록을 작성·보관하여야 한다" 같은 청크가 전체 문서의 어느 맥락
(소농직불 자격 절차 vs 부정수급 조사 절차 vs 일반 의무) 에 속하는지 임베딩
공간에서 모호해진다. Anthropic 의 처방: 색인 시 LLM 으로 "이 청크가 전체
문서에서 어떤 역할을 하는가" 1-2 문장 맥락을 생성해 prepend → 임베딩.
보고된 효과: retrieval failure rate 49% 감소.

LLM 선택:
    OpenRouter 경유 Claude Haiku 4.5 (anthropic/claude-haiku-4-5).
    Anthropic 이 Contextual Retrieval 을 Claude 와 함께 설계했고, 이 프롬프트
    템플릿도 Anthropic 가이드 기반. Haiku 4.5 는 한국어 강함·저렴·빠름·결정론적
    (temperature=0). 277 leaf 전체 재인덱싱 ~$0.04, 캐시 hit 시 $0.

캐시 백엔드:
    Redis (REDIS_URL 설정 시): HASH ``ctx:gov:<sha1(content)>`` 에 저장.
    content-hash 키 → 본문이 1글자라도 바뀌면 자동 무효화. 이전 JSON 파일에서
    있었던 "PDF 교체 시 cache 삭제 권장" 문제 해결.
    Redis 비활성: JSON 파일 폴백 (data/gov/contextual_prefix_cache.json) — 기존
    동작 유지로 dev/CI 에서 Redis 없이도 작동.

실패 모드:
    LLM 호출 실패 → 빈 문자열 반환. gov_rag 는 빈 prefix 면 기존 정적 breadcrumb
    만 사용 (graceful degradation).
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import httpx
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

from app.core.config import settings

if TYPE_CHECKING:
    from app.services.subsidy.chunker import Chunk, LeafChunk

logger = logging.getLogger(__name__)

CACHE_PATH = Path("data/gov/contextual_prefix_cache.json")


# ── 캐시 백엔드 프로토콜 ────────────────────────────────────


class _CacheBackend(Protocol):
    def get(self, key: str) -> str | None: ...
    def set(self, key: str, prefix: str) -> None: ...
    def save(self) -> None: ...


def _content_hash(content: str) -> str:
    """sha1 of content, used as cache key.

    Hashing the *content* (not chunk_id) means the cache auto-invalidates when
    a leaf's text changes — even if the leaf id (e.g. ``CH1_S011#03``) stays
    the same after a re-chunk. This solves the long-standing bug where stale
    prefixes from a prior PDF version mismatched new content.
    """
    # Normalise whitespace so trivial reformatting doesn't bust the cache.
    normalized = " ".join(content.split())
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()


# ── Redis-backed 캐시 (preferred) ───────────────────────────


_sync_redis_client = None  # type: ignore[var-annotated] — lazy module-singleton


def _get_sync_redis():
    """Lazy-init a sync redis-py client for ingest-time cache reads/writes.

    Why sync (not the async client in app.core.redis):
        Ingest runs from a CLI script or FastAPI ``asyncio.to_thread`` — both
        are sync contexts. Using the async client would force an asyncio.run
        per call (creates+destroys event loops, ~10x slower than sync).
        Hot search path uses the async client; ingest stays sync for clarity.

    Connection is reused across calls (singleton). On any error, we drop the
    cached client so the next call re-tries with a fresh connection.
    """
    global _sync_redis_client
    if _sync_redis_client is not None:
        return _sync_redis_client
    if not settings.REDIS_URL:
        return None
    try:
        import redis as _redis_sync

        _sync_redis_client = _redis_sync.Redis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_timeout=settings.REDIS_SOCKET_TIMEOUT_SEC,
            socket_connect_timeout=settings.REDIS_CONNECT_TIMEOUT_SEC,
        )
        # Sanity ping — fail fast if creds wrong, before doing 277 HSET ops.
        _sync_redis_client.ping()
        return _sync_redis_client
    except Exception as e:  # noqa: BLE001
        logger.warning("ctx_cache.sync_client_init_failed err=%s — falling back to JSON", e)
        _sync_redis_client = None
        return None


def _reset_sync_redis() -> None:
    """Drop the cached sync client. Used by call sites when a hget/hset raises
    so the next call rebuilds with a fresh connection (Redis bounces, network
    flap, etc.) instead of being stuck on a poisoned singleton."""
    global _sync_redis_client
    client = _sync_redis_client
    _sync_redis_client = None
    if client is not None:
        try:
            client.close()
        except Exception:  # noqa: BLE001 — best-effort
            pass


class _RedisPrefixCache:
    """Redis HASH 기반 contextual prefix 캐시.

    Layout: HSET ctx:gov:<sha1(content)> p <text>
    Sub-prefix ``ctx:gov:`` keeps prefixes separate from ``emb:*``,
    ``rag:*``, ``sub:gov:`` (leaves) namespaces.

    No TTL — prefixes are deterministic and cheap; we never want to re-pay
    LLM cost. Stale entries become unreachable when content changes (the
    sha1 hash key changes), so they sit harmlessly until manually flushed.
    """

    KEY_PREFIX = "ctx:gov:"

    def get(self, key: str) -> str | None:
        client = _get_sync_redis()
        if client is None:
            return None
        try:
            return client.hget(self.KEY_PREFIX + key, "p")
        except Exception as e:  # noqa: BLE001 — cache failures non-fatal
            logger.warning("ctx_cache.redis_get_failed key=%s err=%s", key, e)
            # Drop the cached client so a transient outage doesn't permanently
            # poison the singleton. Next call re-initialises with a fresh
            # connection.
            _reset_sync_redis()
            return None

    def set(self, key: str, prefix: str) -> None:
        client = _get_sync_redis()
        if client is None:
            return
        try:
            client.hset(self.KEY_PREFIX + key, mapping={"p": prefix})
        except Exception as e:  # noqa: BLE001
            logger.warning("ctx_cache.redis_set_failed key=%s err=%s", key, e)
            _reset_sync_redis()

    def save(self) -> None:
        # Redis writes are individually durable — no batch save needed.
        pass


# ── JSON 파일 fallback (Redis 비활성 또는 dev) ──────────────


class _JsonFilePrefixCache:
    """JSON 파일 캐시 — Redis 가 없을 때 폴백.

    keyed by content-hash (Redis 백엔드와 동일 형식). 같은 인덱싱을
    Redis on/off 에서 번갈아 돌려도 캐시가 호환된다.
    """

    def __init__(self, path: Path = CACHE_PATH) -> None:
        self.path = path
        self._data: dict[str, str] = {}
        self._dirty = False
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            self._data = json.loads(self.path.read_text(encoding="utf-8"))
            logger.info("ctx_cache.json.loaded n=%d path=%s", len(self._data), self.path)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("ctx_cache.json.corrupt — starting fresh err=%s", e)
            self._data = {}

    def get(self, key: str) -> str | None:
        return self._data.get(key)

    def set(self, key: str, prefix: str) -> None:
        if self._data.get(key) != prefix:
            self._data[key] = prefix
            self._dirty = True

    def save(self) -> None:
        if not self._dirty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._dirty = False
        logger.info("ctx_cache.json.saved n=%d path=%s", len(self._data), self.path)


# ── Public cache facade — auto-selects backend ─────────────


class ContextualPrefixCache:
    """Backwards-compatible facade that picks Redis or JSON fallback.

    Preserves the old ``ContextualPrefixCache(path=...)`` constructor signature
    used by call sites in gov_rag.index_chunks, but the ``path`` arg is now
    only used when Redis is disabled.

    Redis selection: ``REDIS_URL`` set AND the sync client connects+pings
    successfully. The async client (app.core.redis.get_redis) is not consulted
    here because ingest runs in sync contexts (CLI / asyncio.to_thread) where
    the async pool may not be initialised.
    """

    def __init__(self, path: Path = CACHE_PATH) -> None:
        if settings.REDIS_URL and _get_sync_redis() is not None:
            self._backend: _CacheBackend = _RedisPrefixCache()
            self._backend_name = "redis"
        else:
            self._backend = _JsonFilePrefixCache(path=path)
            self._backend_name = "json"
        logger.info("ctx_cache.backend selected=%s", self._backend_name)

    def get(self, key: str) -> str | None:
        return self._backend.get(key)

    def set(self, key: str, prefix: str) -> None:
        self._backend.set(key, prefix)

    def save(self) -> None:
        self._backend.save()


# ── 프롬프트 ────────────────────────────────────────────────


def _build_context_prompt(chunk: "Chunk | LeafChunk") -> str:
    """청크 맥락 생성용 프롬프트.

    설계: Anthropic Contextual Retrieval 공식 템플릿 (XML 태그 + "answer only"
    지시) 을 베이스로, 농림축산식품부 공익직불제 도메인 정보를 <document>
    블록에 압축하고, 4개의 few-shot example 로 출력 형식을 anchor.

    Chunk 와 LeafChunk 모두에서 작동 — 두 dataclass 모두 chapter, section,
    subsection_title, content 필드를 가짐 (structural typing).
    """
    prompt = (
        "<document>\n"
        "2026년도 기본형 공익직불사업 시행지침 — 농림축산식품부가 발간하는 공익직불제 운영 매뉴얼.\n"
        "\n"
        "지원금 종류:\n"
        "- 소농직불금: 정액 130만원, 면적·소득·경력 등 9개 자격요건 모두 충족 시.\n"
        "- 면적직불금: 면적 구간별 단가, 진흥지역/비진흥, 논/밭/과수에 따라 차등.\n"
        "\n"
        "핵심 주제 영역:\n"
        "- 자격요건: 농지 (면적·유형·진흥지역 여부), 농업인 (영농경력·농촌거주연수·종합소득금액·농가 구성원 합산소득).\n"
        "- 농업인 8대 준수사항: 농업경영체 등록 / 영농기록 작성·보관 / 농약·화학비료 사용기준 / "
        "영농폐기물 수거·처리 / 농업·농촌 공익기능 증진 교육 이수 / 마을공동체 활동 / "
        "농지 형상·기능 유지 / 가축분뇨 공공수역 배출 금지.\n"
        "- 부정수급 처분: 환수, 최대 5배 추가징수, 신청제한 (1차 3년·2차 5년·3차 8년), 신고포상금.\n"
        "- 지급 절차: 신청 → 자격검증 (정보분석·차단) → 농작업 직접수행 현장조사 → 마을 자격검증위원회 → 지급.\n"
        "- 감액지급: 준수사항 위반 시 단계별 감액 (위반 항목·횟수·고의성 기준).\n"
        "\n"
        "농업인 유형: 일반, 청년농업인, 후계농업인, 전업농업인, 2030세대, 신규신청자, 귀농인.\n"
        "</document>\n"
        "\n"
        "<chunk_location>\n"
        f"{chunk.chapter} > {chunk.section} > {chunk.subsection_title}\n"
        "</chunk_location>\n"
        "\n"
        "<chunk>\n"
        f"{chunk.content[:2500]}\n"
        "</chunk>\n"
        "\n"
        "위 청크가 시행지침 전체에서 어떤 역할을 하는지, 검색 정확도 향상을 위한 1~2 문장 맥락을 한국어로 작성하세요.\n"
        "\n"
        "# 작성 규칙\n"
        "- 50~120자, 1~2 문장. 핵심 키워드 위주.\n"
        "- 어떤 지원금 (소농직불·면적직불·공익직불 일반) 인지 식별.\n"
        "- 어떤 의도 (정의·자격요건·지급단가·지급액·의무·금지·부정수급 처분·신청절차·예외·서식·감액) 인지 분류.\n"
        "- 본문에 등장하는 핵심 명사 (영농기록·종합소득금액·진흥지역·역전구간·청년농업인·환수·행정처분·신청제한 등) 를 포함.\n"
        "- 본문에 없는 내용을 추측·발명하지 마세요.\n"
        "- 출력은 맥락 요약문 하나만. \"맥락:\", \"이 청크는~\", \"본 조항은~\" 같은 prefix 금지.\n"
        "\n"
        "# 예시\n"
        "<example_chunk>\n"
        "위치: CHAPTER 1 > II. 기본직불금 자격요건 > 3. 소농직불 지급대상 자격요건\n"
        "본문: 소농직불금 지급대상 자격을 갖추기 위해서는 다음 요건을 모두 충족하여야 한다. "
        "① 농지 면적 0.1~0.5ha. ② 영농 경력 3년 이상. ③ 농촌 거주 3년 이상. "
        "④ 농업경영체 등록 완료. ⑤ 신청자 개인의 농업 외 종합소득이 2,000만원 미만 ...\n"
        "</example_chunk>\n"
        "<example_summary>\n"
        "소농직불금 지급대상 자격요건 — 면적 (0.1~0.5ha), 영농경력 3년, 농촌거주 3년, 농업경영체 등록, 종합소득금액 상한 등 9개 충족 조건을 규정.\n"
        "</example_summary>\n"
        "\n"
        "<example_chunk>\n"
        "위치: CHAPTER 2 > II > 7. 부정수급 처분 기준\n"
        "본문: 거짓이나 그 밖의 부정한 방법으로 직불금을 받은 경우, 환수하고 1차 위반 시 3년, 2차 5년, "
        "3차 8년의 신청제한 처분을 부과한다. 지급액의 최대 5배까지 추가 징수가 가능하다 ...\n"
        "</example_chunk>\n"
        "<example_summary>\n"
        "부정수급 행정처분 — 환수, 신청제한 (1차 3년·2차 5년·3차 8년), 최대 5배 추가징수 등 단계별 처분 기준을 규정.\n"
        "</example_summary>\n"
        "\n"
        "<example_chunk>\n"
        "위치: CHAPTER 1 > III. 농업인 준수사항 > 5. 영농기록 작성·보관 의무\n"
        "본문: 농업인은 영농활동을 입증할 수 있는 영농기록을 작성·보관하여야 한다. 부정수급 의심자에 대한 "
        "조사 시 영농기록 또는 농자재 구입 영수증 등을 제출하여야 한다 ...\n"
        "</example_chunk>\n"
        "<example_summary>\n"
        "농업인 준수사항 — 영농기록 작성·보관 의무, 부정수급 조사 시 영농기록·농자재 영수증 제출 의무를 규정.\n"
        "</example_summary>\n"
        "\n"
        "<example_chunk>\n"
        "위치: CHAPTER 1 > IV. 지급단가 > 2. 면적직불금 지급단가표\n"
        "본문: 면적직불금 지급단가는 농지 유형 및 면적구간에 따라 차등 적용된다. 진흥지역 논 1구간 "
        "(2ha 이하) 215만원/ha, 진흥지역 밭 1구간 205만원/ha, 비진흥지역 논 1구간 178만원/ha ...\n"
        "</example_chunk>\n"
        "<example_summary>\n"
        "면적직불금 지급단가표 — 농지유형 (논·밭·과수)·진흥지역 여부·면적구간별 ha당 단가를 표 형식으로 제시.\n"
        "</example_summary>\n"
        "\n"
        "요약:"
    )
    return prompt


# ── 단일 청크 prefix 생성 ──────────────────────────────────


_LLM_SINGLETON: tuple[ChatOpenAI | None, str] | None = None


def _build_llm() -> tuple[ChatOpenAI | None, str]:
    """OpenRouter (claude-haiku-4-5) 우선, fallback to LiteLLM (gemma).

    Returns (llm, label). The httpx.Client used to live inline per call —
    that leaked file descriptors because `with` was removed in the rewrite
    and only GC eventually closed it. Cache the LLM (and its underlying
    HTTP client) as a module-level singleton so connections are pooled and
    cleanly held for the process lifetime.
    """
    global _LLM_SINGLETON
    if _LLM_SINGLETON is not None:
        return _LLM_SINGLETON

    if settings.OPENROUTER_API_KEY and settings.SUBSIDY_CONTEXTUAL_LLM_MODEL:
        # OpenRouter is OpenAI-compatible — ChatOpenAI works as the client.
        # No streaming; one-shot completion of ~120 tokens.
        http_client = httpx.Client(
            http1=True, http2=False, timeout=httpx.Timeout(30.0, connect=10.0),
        )
        llm = ChatOpenAI(
            model=settings.SUBSIDY_CONTEXTUAL_LLM_MODEL,
            base_url=settings.OPENROUTER_BASE_URL,
            api_key=settings.OPENROUTER_API_KEY,
            temperature=0.0,
            max_tokens=160,
            http_client=http_client,
            # OpenRouter recommends app-identifying headers — not required but
            # avoids being flagged as anon traffic.
            default_headers={
                "HTTP-Referer": "https://github.com/FarmOS-v2",
                "X-Title": "FarmOS Subsidy RAG",
            },
        )
        _LLM_SINGLETON = (llm, f"openrouter:{settings.SUBSIDY_CONTEXTUAL_LLM_MODEL}")
        return _LLM_SINGLETON

    if settings.LITELLM_API_KEY and settings.SUBSIDY_LLM_MODEL:
        http_client = httpx.Client(
            http1=True, http2=False, timeout=httpx.Timeout(30.0, connect=10.0),
        )
        llm = ChatOpenAI(
            model=settings.SUBSIDY_LLM_MODEL,
            base_url=settings.LITELLM_URL,
            api_key=settings.LITELLM_API_KEY,
            temperature=0.0,
            max_tokens=120,
            http_client=http_client,
        )
        _LLM_SINGLETON = (llm, f"litellm:{settings.SUBSIDY_LLM_MODEL}")
        return _LLM_SINGLETON

    _LLM_SINGLETON = (None, "(no llm configured)")
    return _LLM_SINGLETON


def generate_contextual_prefix(
    chunk: "Chunk | LeafChunk", cache: ContextualPrefixCache,
) -> str:
    """청크에 대한 contextual prefix 를 LLM 으로 생성. 캐시 우선.

    캐시 키: sha1(normalized_content) — 본문 변경 시 자동 무효화.
    실패 시 빈 문자열 반환 — 호출자는 정적 breadcrumb 만으로 fallback 가능.

    Note: 동기 함수 — gov_rag.index_chunks 가 ``asyncio.to_thread`` 로 감쌈.
    """
    cache_key = _content_hash(chunk.content)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    llm, label = _build_llm()
    if llm is None:
        logger.warning("ctx_prefix.no_llm — set OPENROUTER_API_KEY or LITELLM_API_KEY")
        return ""

    prompt = _build_context_prompt(chunk)
    try:
        response = llm.invoke([HumanMessage(content=prompt)])
        text = response.content if isinstance(response.content, str) else ""
        text = text.strip().strip('"\'').replace("\n", " ")

        # 안전장치: 비거나 너무 길면 캐시 안 함 → 다음 인덱싱에서 재시도.
        if not text or len(text) > 400:
            logger.info("ctx_prefix.unfit chunk_id=%s text=%r", chunk.id, text[:80])
            return ""

        cache.set(cache_key, text)
        return text
    except Exception as e:  # noqa: BLE001 — graceful fallback
        logger.warning(
            "ctx_prefix.gen_failed chunk_id=%s llm=%s err=%s: %s",
            chunk.id, label, type(e).__name__, e,
        )
        return ""
