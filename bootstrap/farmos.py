#!/usr/bin/env python
"""FarmOS DB/테이블 초기화 스크립트."""

from __future__ import annotations

import argparse
import os
import re

from _bootstrap_common import (  # type: ignore[import-not-found]
    BACKEND_DIR,
    ROOT,
    BootstrapError,
    detect_database_url,
    ensure_database_exists,
    ensure_postgres_running,
    ensure_tools,
    error,
    info,
    parse_database_url,
    print_table_summary,
    psql_query,
    run_command,
    set_log_prefix,
    table_exists,
)

FARMOS_TABLES = [
    "users",
    "journal_entries",
    "rag_pesticide_products",
    "rag_pesticide_crops",
    "rag_pesticide_targets",
    "rag_pesticide_product_applications",
    "rag_pesticide_documents",
    "review_analyses",
    "review_sentiments",
]
LOG_PREFIX = "FarmOS"
EXPECTED_ROW_COUNTS = {
    "users": 2,
}


def _to_asyncpg_url(raw_db_url: str) -> str:
    """driver 부분을 FarmOS 비동기 엔진용 URL로 맞춘다."""
    if raw_db_url.startswith("postgres://"):
        return raw_db_url.replace("postgres://", "postgresql+asyncpg://", 1)
    url = re.sub(r"^postgresql\+\w+://", "postgresql+asyncpg://", raw_db_url, count=1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


def uv_sync_backend(skip_sync: bool) -> None:
    if skip_sync:
        info("uv sync 생략 (--skip-sync)")
        return
    info("FarmOS backend 의존성 동기화(uv sync) - 시간이 많이 걸릴 수 있습니다")
    run_command(["uv", "sync"], cwd=BACKEND_DIR)


def _quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def all_farmos_tables_exist(db_conf: dict[str, str]) -> bool:
    return all(table_exists(db_conf, table) for table in FARMOS_TABLES)


def drop_farmos_tables(db_conf: dict[str, str]) -> None:
    info("FarmOS 스키마 재구성: 기존 테이블 삭제(drop)")
    # legacy mock 테이블 포함
    targets = [*FARMOS_TABLES, "pesticide_products"]
    quoted_targets = ", ".join(_quote_identifier(table) for table in targets)
    drop_sql = f"DROP TABLE IF EXISTS {quoted_targets} CASCADE;"
    psql_query(db_conf, drop_sql)


def truncate_farmos_tables(db_conf: dict[str, str]) -> None:
    # legacy mock 테이블 포함, 실제 존재하는 테이블만 truncate한다.
    candidate_tables = [*FARMOS_TABLES, "pesticide_products"]
    existing_tables = [
        table for table in candidate_tables if table_exists(db_conf, table)
    ]
    if not existing_tables:
        info("truncate 대상 FarmOS 테이블이 없습니다.")
        return

    info("FarmOS 데이터 비우기(truncate)")
    targets = ", ".join(_quote_identifier(table) for table in existing_tables)
    truncate_sql = (
        "BEGIN; "
        "SET LOCAL lock_timeout = '5s'; "
        f"TRUNCATE TABLE {targets} RESTART IDENTITY CASCADE; "
        "COMMIT;"
    )
    psql_query(db_conf, truncate_sql)


def run_farmos_seed(async_db_url: str) -> None:
    """실제 스키마 생성 + 기본 유저 시드를 farmos_seed.py에 위임한다."""
    info("FarmOS 스키마/시드 적용")
    seed_script = ROOT / "bootstrap" / "farmos_seed.py"
    run_command(
        ["uv", "run", "python", str(seed_script)],
        cwd=BACKEND_DIR,
        env_overrides={"DATABASE_URL": async_db_url},
    )


def run_pesticide_loader(raw_db_url: str, append_mode: bool = True) -> None:
    info("농약 RAG 테이블 적재 스크립트 실행")
    loader_script = ROOT / "bootstrap" / "pesticide.py"
    json_dir = ROOT / "tools" / "api-crawler" / "json_raw"
    command = [
        "--db-url",
        raw_db_url,
        "--input-dir",
        str(json_dir),
    ]
    if append_mode:
        command.append("--append")
    venv_python = (
        BACKEND_DIR / ".venv" / "Scripts" / "python.exe"
        if os.name == "nt"
        else BACKEND_DIR / ".venv" / "bin" / "python"
    )
    if venv_python.exists():
        run_command([str(venv_python), str(loader_script), *command], cwd=BACKEND_DIR)
        return
    run_command(["uv", "run", "python", str(loader_script), *command], cwd=BACKEND_DIR)


def is_farmos_ready(db_conf: dict[str, str]) -> bool:
    """운영에 필요한 최소 상태를 확인한다.

    기준:
    - 필수 테이블 존재
    - users 테이블에 테스트 계정 2명 이상 존재
    """
    for table in FARMOS_TABLES:
        if not table_exists(db_conf, table):
            return False
    for table, expected in EXPECTED_ROW_COUNTS.items():
        actual = int(psql_query(db_conf, f"SELECT COUNT(*) FROM {table};") or "0")
        if actual < expected:
            return False
    return True


def print_summary(db_conf: dict[str, str], verbose_table_info: bool) -> None:
    print_table_summary(
        db_conf,
        "FarmOS",
        FARMOS_TABLES,
        verbose_table_info=verbose_table_info,
    )


def initialize(
    db_conf: dict[str, str],
    raw_db_url: str,
    skip_sync: bool,
    force_rebuild_schema: bool = False,
) -> None:
    uv_sync_backend(skip_sync)
    rebuild_schema = force_rebuild_schema or (not all_farmos_tables_exist(db_conf))
    if rebuild_schema:
        if force_rebuild_schema:
            info("사용자 요청으로 FarmOS 스키마 재구성 모드 실행 (--rebuild-schema)")
        else:
            info("FarmOS 필수 테이블 일부 누락 감지 (스키마 재구성 모드)")
        drop_farmos_tables(db_conf)
    else:
        truncate_farmos_tables(db_conf)
    run_farmos_seed(_to_asyncpg_url(raw_db_url))
    run_pesticide_loader(raw_db_url, append_mode=not rebuild_schema)


def main() -> int:
    parser = argparse.ArgumentParser(description="FarmOS PostgreSQL 초기화")
    parser.add_argument("--database-url", help="DATABASE_URL 강제 지정")
    parser.add_argument("--skip-sync", action="store_true", help="uv sync 생략")
    parser.add_argument(
        "--mode",
        choices=("init", "ensure"),
        default="init",
        help="init=항상 재초기화, ensure=필요할 때만 초기화",
    )
    parser.add_argument(
        "--rebuild-schema",
        action="store_true",
        help="초기화 시 스키마를 강제 재생성(drop/create)합니다.",
    )
    parser.add_argument(
        "--verbose-table-info",
        action="store_true",
        help="테이블 컬럼/row 수 상세 정보를 출력",
    )
    args = parser.parse_args()

    try:
        set_log_prefix(LOG_PREFIX)
        ensure_tools("uv", "psql")
        raw_db_url = detect_database_url(args.database_url, prefer="farmos")
        db_conf = parse_database_url(raw_db_url)

        ensure_postgres_running(db_conf)
        ensure_database_exists(db_conf)

        initialized = args.mode == "init"
        if args.mode == "ensure":
            if args.rebuild_schema:
                info("사용자 요청으로 강제 초기화 수행 (--rebuild-schema)")
                initialize(
                    db_conf,
                    raw_db_url,
                    args.skip_sync,
                    force_rebuild_schema=True,
                )
                initialized = True
            elif is_farmos_ready(db_conf):
                info("FarmOS DB 상태 정상 (초기화 생략)")
            else:
                info("FarmOS DB 상태 불완전 (초기화 수행)")
                initialize(db_conf, raw_db_url, args.skip_sync)
                initialized = True
        else:
            initialize(
                db_conf,
                raw_db_url,
                args.skip_sync,
                force_rebuild_schema=args.rebuild_schema,
            )
        if initialized:
            print_summary(db_conf, args.verbose_table_info)
            print()
            info("FarmOS 데이터베이스 초기화 완료")
        else:
            info("FarmOS 데이터베이스 상태 확인 완료")
        return 0
    except BootstrapError as exc:
        error(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
