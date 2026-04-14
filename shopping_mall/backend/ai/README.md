# ai/

AI 기능 모음. 크게 **Ollama 기반 유틸리티**와 **에이전트 서브패키지**로 나뉩니다.

## 파일

| 파일 | 역할 |
|------|------|
| `llm_client.py` | Ollama LLM 클라이언트 — 리포트 생성(`generate_report`), 비용 분류(`classify_expense`) 전용. 챗봇 에이전트와는 무관 |
| `rag.py` | ChromaDB 검색 서비스 — `retrieve()` / `retrieve_multiple()`로 관련 문서 반환. 답변 생성은 에이전트가 담당 |
| `seed_rag.py` | ChromaDB 초기 데이터 적재 스크립트. JSON 및 PDF/DOCX를 파싱해 9개 컬렉션에 upsert |

## 서브패키지

| 디렉터리 | 역할 |
|----------|------|
| `agent/` | tool_use 에이전트 전체 (executor, 도구, 클라이언트, 프롬프트) |
| `data/` | RAG JSON 원본 데이터 (`faq.json`, `storage_guide.json`, `season_info.json`) |

## ChromaDB 컬렉션

```
faq / storage_guide / season_info          ← JSON (ai/data/)
payment_policy / delivery_policy /
return_policy / quality_policy /           ← PDF/DOCX (.claude/docs/)
service_policy / membership_policy
```

## 자주 쓰는 명령

```bash
# RAG 시딩
uv run python ai/seed_rag.py

# 시딩 + 검색 검증 한번에
uv run python scripts/seed_and_verify.py
```
