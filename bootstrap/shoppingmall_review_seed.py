#!/usr/bin/env python
# ruff: noqa: E402
# pyright: reportMissingImports=false, reportMissingModuleSource=false
"""ShoppingMall 리뷰 1,000건 재시드 스크립트.

기본 쇼핑몰 시드 이후 실행되어 shop_reviews를 재생성한다.
"""

from __future__ import annotations

import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import text
from _bootstrap_common import error, info, set_log_prefix  # type: ignore[import-not-found]

ROOT = Path(__file__).resolve().parents[1]
SHOP_BACKEND_DIR = ROOT / "shopping_mall" / "backend"
if str(SHOP_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(SHOP_BACKEND_DIR))

from app.database import SessionLocal

SEED_RANDOM = 42
TARGET_COUNT = 1000
SENTIMENT_SPLIT = {"positive": 0.50, "negative": 0.25, "neutral": 0.25}
LOG_PREFIX = "SHOP-R-SEED"

POSITIVE_TEMPLATES = [
    "정말 맛있어요! {product} 품질이 최고입니다.",
    "{product} 너무 신선하고 좋아요. 재구매 의사 100%!",
    "배송도 빠르고 {product} 품질도 좋아서 만족합니다.",
    "가격 대비 품질이 너무 좋아요. {product} 추천합니다!",
]
NEUTRAL_TEMPLATES = [
    "{product} 보통이에요. 가격 대비 무난합니다.",
    "맛은 괜찮은데 양이 조금 아쉬워요.",
    "그냥 평범한 {product}이에요.",
]
NEGATIVE_TEMPLATES = [
    "{product} 포장이 엉망이에요. 배송 중 상했어요.",
    "기대 이하입니다. {product} 신선도가 많이 떨어져요.",
    "배송이 늦어서 상태가 좋지 않았어요.",
]


def _load_ids(db) -> tuple[list[int], list[int], dict[int, str]]:
    product_rows = db.execute(
        text("SELECT id, name FROM shop_products ORDER BY id")
    ).fetchall()
    user_rows = db.execute(text("SELECT id FROM shop_users ORDER BY id")).fetchall()
    product_ids = [int(r[0]) for r in product_rows]
    user_ids = [int(r[0]) for r in user_rows]
    product_names = {int(r[0]): str(r[1]) for r in product_rows}
    if not product_ids or not user_ids:
        raise RuntimeError(
            "shop_products 또는 shop_users가 비어 있어 리뷰를 생성할 수 없습니다."
        )
    return product_ids, user_ids, product_names


def _build_sentiments(total: int) -> list[str]:
    pos = int(total * SENTIMENT_SPLIT["positive"])
    neg = int(total * SENTIMENT_SPLIT["negative"])
    neu = total - pos - neg
    sentiments = (["positive"] * pos) + (["negative"] * neg) + (["neutral"] * neu)
    random.shuffle(sentiments)
    return sentiments


def _build_review_row(
    idx: int,
    sentiment: str,
    product_ids: list[int],
    user_ids: list[int],
    product_names: dict[int, str],
) -> dict:
    pid = random.choice(product_ids)
    pname = product_names.get(pid, "상품")
    if sentiment == "positive":
        rating = random.choice([4.0, 4.0, 5.0, 5.0, 5.0])
        template = random.choice(POSITIVE_TEMPLATES)
    elif sentiment == "negative":
        rating = random.choice([1.0, 1.0, 2.0, 2.0])
        template = random.choice(NEGATIVE_TEMPLATES)
    else:
        rating = 3.0
        template = random.choice(NEUTRAL_TEMPLATES)
    return {
        "id": idx,
        "product_id": pid,
        "user_id": random.choice(user_ids),
        "rating": rating,
        "content": template.format(product=pname),
        "created_at": datetime.utcnow() - timedelta(days=random.randint(0, 90)),
    }


def reseed_reviews(total: int = TARGET_COUNT) -> None:
    random.seed(SEED_RANDOM)
    db = SessionLocal()
    try:
        product_ids, user_ids, product_names = _load_ids(db)
        db.execute(text("DELETE FROM shop_reviews"))
        sentiments = _build_sentiments(total)
        payload = [
            _build_review_row(i, sentiment, product_ids, user_ids, product_names)
            for i, sentiment in enumerate(sentiments, start=1)
        ]
        db.execute(
            text(
                """
                INSERT INTO shop_reviews (id, product_id, user_id, rating, content, created_at)
                VALUES (:id, :product_id, :user_id, :rating, :content, :created_at)
                """
            ),
            payload,
        )
        db.commit()
        final_count = (
            db.execute(text("SELECT COUNT(*) FROM shop_reviews")).scalar() or 0
        )
        info(f"ShoppingMall 리뷰 재시드 완료: {final_count} rows")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main() -> int:
    set_log_prefix(LOG_PREFIX)
    try:
        reseed_reviews(TARGET_COUNT)
        return 0
    except Exception as exc:
        error(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
