# ai/

AI 기능 모음. 크게 **유틸리티**와 **에이전트 서브패키지**로 나뉩니다.

## 파일

| 파일 | 역할 |
|------|------|
| `embeddings.py` | 임베딩 함수 팩토리 — `EMBED_PROVIDER` 설정에 따라 provider 선택 |
| `llm_client.py` | Utility LLM 클라이언트 — 리포트 생성·비용 분류 전용. 챗봇 에이전트와 무관 |
| `rag.py` | ChromaDB 검색 서비스 — `retrieve()` / `retrieve_multiple()`로 관련 문서 반환 |
| `seed_rag.py` | ChromaDB 초기 데이터 적재 스크립트. JSON 및 PDF/DOCX를 파싱해 컬렉션에 upsert |

## 서브패키지

| 디렉터리 | 역할 |
|----------|------|
| `agent/` | tool_use 에이전트 전체 (executor, 도구, 클라이언트, 프롬프트) |
| `data/` | RAG JSON 원본 데이터 (`faq.json`, `storage_guide.json`, `season_info.json`) |
| `docs/` | 정책 문서 PDF/DOCX — gitignore 처리, 로컬에 직접 배치 필요 |

## ChromaDB 컬렉션

```
faq / storage_guide / season_info /        ← JSON (ai/data/)
farm_intro
payment_policy / delivery_policy /
return_policy / quality_policy /           ← PDF/DOCX (ai/docs/)
service_policy / membership_policy
```

| 컬렉션 | 데이터 소스 | 내용 |
|--------|------------|------|
| `faq` | `ai/data/faq.json` | 자주 묻는 질문 |
| `storage_guide` | `ai/data/storage_guide.json` | 농산물 보관법 |
| `season_info` | `ai/data/season_info.json` | 제철 정보 |
| `farm_intro` | `ai/data/farm_info.json` | 플랫폼 소개·품질 인증·협력 농가·친환경 등 |
| `*_policy` | `ai/docs/*.pdf / *.docx` | 결제·배송·반품·품질·서비스·멤버십 정책 |

> `farm_intro`가 없으면 `search_farm_info` 도구가 항상 fallback 메시지를 반환합니다.  
> `uv run python ai/seed_rag.py` 실행 시 자동으로 포함됩니다.

---

## 팀원 셋업 가이드

### 1단계 — 임베딩 provider 선택

`.env`에서 본인 환경에 맞는 옵션을 선택합니다.
**시딩과 서버 실행 시 반드시 동일한 provider + model을 사용해야 합니다.**

**OpenRouter 키가 있는 경우 (권장)**
```env
EMBED_PROVIDER=openrouter
EMBED_MODEL=openai/text-embedding-3-small
# PRIMARY_LLM_API_KEY를 자동으로 재사용 — 추가 키 불필요
```

**OpenRouter 키가 없는 경우 — 로컬 실행 (API 키·서버 불필요)**
```env
EMBED_PROVIDER=sentence_transformers
EMBED_MODEL=BAAI/bge-m3
```
```bash
uv add sentence-transformers   # 최초 1회
```
> 첫 실행 시 HuggingFace에서 모델을 자동 다운로드합니다 (~400MB).

### 2단계 — 정책 문서 배치

`ai/docs/` 폴더에 정책 문서(PDF/DOCX)를 넣습니다.
폴더가 없으면 직접 생성하세요 (gitignore 처리되어 있습니다).

```
ai/docs/
├── 01_주문및결제정책.pdf
├── 02_배송정책.docx
└── ...
```

### 3단계 — 시딩

```bash
uv run python ai/seed_rag.py

# 시딩 + 검색 검증 한번에
uv run python scripts/seed_and_verify.py
```

### BM25 한국어 토크나이저

`seed_rag.py`의 `_tokenize_ko()`는 정규식 기반 토크나이저를 사용합니다.
인덱스 빌드(seed)와 검색 쿼리 토큰화를 동일한 방식으로 통일하여 일관성을 보장합니다.

```python
# 한글·영문·숫자 단위로 분리
re.findall(r"[가-힣a-zA-Z0-9]+", text.lower())
# "딸기를 보관하는 방법" → ["딸기를", "보관하는", "방법"]
```
