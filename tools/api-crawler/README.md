# API Crawler (FarmOS)

식품안전나라 `I1910` 데이터를 수집(`crawler.py`)하고 전처리(`preprocessor.py`)하여 SQLite/PostgreSQL에 적재합니다.

## 구성 파일

- `crawler.py`: API 원본 JSON 수집 (`json_raw/`) + 선택적으로 SQLite `pesticide_rows` 적재
- `preprocessor.py`: 원본 JSON을 정제해 `products`, `crops`, `targets`, `product_applications`, `rag_documents` 적재
- `pyproject.toml`: 실행 의존성 정의

## 사전 준비 (uv)

```powershell
cd path/to/FarmOS/tools/api-crawler
uv sync
```

기본 env 파일 경로:
- `path/to/FarmOS/tools/backend/.env`

필수 키:
- `FOOD_SAFETY_API_KEY` (crawler)
- `DATABASE_URL` (preprocessor PostgreSQL)

`DATABASE_URL`은 `postgresql+asyncpg://...` 형식도 허용하며 내부에서 `postgresql+psycopg://...`로 변환해 사용합니다.

## 빠른 실행

```powershell
cd path/to/FarmOS/tools/api-crawler

# 1) API 1배치 수집
uv run crawler.py --max-batches 1 --delay-seconds 0

# 2) PostgreSQL 적재 (기본: 테이블 재생성)
uv run preprocessor.py --db-type postgresql --input-dir json_raw --glob 00000-00999.json
```

## 중복 처리 정책

- 기본 모드(`--append` 미사용): 테이블 `drop/create` 후 전체 적재
  - 기존 데이터는 유지되지 않습니다.
- 누적 모드(`--append`): 기존 테이블 유지 + 업서트
  - `products`: `product_id` 기준 update-or-insert
  - `crops`: `crop_name_normalized` 기준 기존값 재사용
  - `targets`: `(target_name_normalized, target_kind)` 기준 기존값 재사용
  - `product_applications`: `(product_id, crop_id, target_id)` 기준 update-or-insert
  - `rag_documents`: `application_id` 기준 update-or-insert

즉, 같은 파일을 `--append`로 재실행해도 중복 insert 에러 없이 갱신됩니다.

## PRDLST_KOR_NM 명칭 처리

원본 API의 `PRDLST_KOR_NM`은 의미상 성분/제형 정보에 가까워,
전처리 결과에서는 `ingredient_or_formulation_name` 컬럼으로 저장합니다.

## 주요 옵션

### crawler.py

- `--env-name`: API 키 환경변수명 (기본 `FOOD_SAFETY_API_KEY`)
- `--env-path`: env 파일 경로 (기본 `backend/.env`)
- `--change-date YYYYMMDD`: 변경일자 이후 데이터만 수집
- `--raw-dir`: raw JSON 출력 디렉터리
- `--disable-db`: SQLite 저장 비활성화
- `--rebuild-db-from-json`: `json_raw/*.json`로 SQLite 재생성

### preprocessor.py

- `--backend-env-path`: DB env 파일 경로 (기본 `backend/.env`)
- `--db-type sqlite|postgresql`: 적재 대상 DB 타입
- `--db-url`: 명시 DB URL (env보다 우선)
- `--append`: 테이블 유지 + 업서트 누적 적재
- `--log-every`: 진행 로그 주기
