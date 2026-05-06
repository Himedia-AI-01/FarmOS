"""shop_products 누락 컬럼 추가 마이그레이션.

모델(`app/models/product.py`)에는 다음 두 컬럼이 정의되어 있으나
실제 DB에는 누락된 상태(드리프트)를 복구한다:

    - is_available         BOOLEAN NOT NULL DEFAULT TRUE
    - low_stock_threshold  INTEGER NOT NULL DEFAULT 10

bootstrap/create_tables.py 는 ADD COLUMN 을 수행하지 않으므로
이 스크립트로 명시적으로 ALTER 한다 (멱등 — IF NOT EXISTS 사용).

사용법:
    uv run python scripts/migrate_product_columns.py --dry-run
    uv run python scripts/migrate_product_columns.py
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, text

from app.core.config import settings

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
logger = logging.getLogger(__name__)


COLUMNS = [
    ("is_available", "BOOLEAN NOT NULL DEFAULT TRUE"),
    ("low_stock_threshold", "INTEGER NOT NULL DEFAULT 10"),
]


def run(dry_run: bool = False) -> None:
    engine = create_engine(settings.database_url)
    mode = "[DRY-RUN]" if dry_run else "[EXECUTE]"

    with engine.begin() as conn:
        existing = {
            row[0]
            for row in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'shop_products'"
                )
            )
        }
        if not existing:
            logger.error("shop_products 테이블이 존재하지 않습니다. 먼저 create_tables.py 실행.")
            sys.exit(1)

        for col, ddl in COLUMNS:
            if col in existing:
                logger.info("%s skip — shop_products.%s 이미 존재", mode, col)
                continue

            stmt = f"ALTER TABLE shop_products ADD COLUMN {col} {ddl}"
            logger.info("%s %s", mode, stmt)
            if not dry_run:
                conn.execute(text(stmt))

        # stock=0 인 행이 있다면 is_available 도 False 로 동기화 (실 데이터 정합성)
        if not dry_run and "is_available" not in existing:
            updated = conn.execute(
                text("UPDATE shop_products SET is_available = FALSE WHERE stock = 0")
            ).rowcount
            logger.info("%s stock=0 인 %d 행의 is_available=FALSE 동기화", mode, updated)

    logger.info("%s 완료", mode)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="변경 없이 계획만 출력")
    args = parser.parse_args()
    run(dry_run=args.dry_run)
