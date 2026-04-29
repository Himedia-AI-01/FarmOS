"""KAMIS (농산물유통정보) API 클라이언트 서비스."""

from __future__ import annotations

import logging
import ssl

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


# KAMIS 서버는 OpenSSL 기본 SECLEVEL=2가 거부하는 레거시 cipher suite로
# TLS 핸드셰이크를 시도한다 (한국 공공 API 전반에서 흔한 이슈).
# SECLEVEL=1로 낮춰 레거시 cipher를 허용하되, 인증서 검증은 그대로 유지.
# 영향: TLS 1.0/1.1 핸드셰이크 가능. KAMIS는 인증 없는 공개 시세 API이므로
# 이 trade-off는 수용 가능 (PII·시크릿 전송 없음).
def _build_kamis_ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.set_ciphers("DEFAULT:@SECLEVEL=1")
    return ctx


_KAMIS_SSL_CTX = _build_kamis_ssl_context()


class KamisService:
    """KAMIS Open API wrapper.

    Endpoints used:
      #1  dailySalesList      — 일별 부류별 도·소매가격정보 (메인)
      #2  periodProductList   — 일별 품목별 도·소매가격정보 (상세)
    """

    BASE_URL = "https://www.kamis.or.kr/service/price/xml.do"
    TIMEOUT = 15  # seconds

    def __init__(self, api_key: str, cert_id: str) -> None:
        self._api_key = api_key
        self._cert_id = cert_id

    # ── helpers ──────────────────────────────────────────────

    def _common_params(self) -> dict[str, str]:
        return {
            "p_cert_key": self._api_key,
            "p_cert_id": self._cert_id,
            "p_returntype": "json",
        }

    async def _get(self, action: str, extra: dict[str, str] | None = None) -> dict:
        params = {"action": action, **self._common_params(), **(extra or {})}
        headers = {"User-Agent": "Mozilla/5.0"}
        # verify=_KAMIS_SSL_CTX 로 KAMIS 레거시 TLS 허용 (위 _build_kamis_ssl_context 주석 참조).
        async with httpx.AsyncClient(
            timeout=self.TIMEOUT, headers=headers, verify=_KAMIS_SSL_CTX
        ) as client:
            try:
                resp = await client.get(self.BASE_URL, params=params)
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as exc:
                logger.warning(
                    "kamis.http_error action=%s status=%s body=%s",
                    action, exc.response.status_code, exc.response.text[:200],
                )
                raise
            except httpx.HTTPError as exc:
                logger.warning(
                    "kamis.connection_error action=%s err=%s", action, exc
                )
                raise

    # ── API #1: 일별 부류별 도·소매가격정보 ─────────────────

    async def get_latest_prices(
        self,
        *,
        product_cls_code: str = "",
        item_category_code: str = "",
        country_code: str = "",
    ) -> list[dict]:
        """최신 가격 목록을 반환한다.

        Returns a flat list of item dicts, each containing:
          product_cls_code, category_code, category_name, productno,
          item_name, unit, day1, dpr1, day2, dpr2, day3, dpr3, day4, dpr4,
          direction, value
        """
        raw = await self._get(
            "dailySalesList",
            {
                "p_product_cls_code": product_cls_code,
                "p_item_category_code": item_category_code,
                "p_country_code": country_code,
            },
        )
        return self._extract_daily_sales(raw)

    # ── API #2: 일별 품목별 도·소매가격정보 ─────────────────

    async def get_daily_prices(
        self,
        start_date: str,
        end_date: str,
        item_code: str,
        kind_code: str,
        rank_code: str = "1",
        *,
        product_cls_code: str = "01",
        item_category_code: str = "",
        county_code: str = "",
        convert_kg: str = "Y",
    ) -> dict:
        """일별 가격 데이터를 반환한다."""
        raw = await self._get(
            "periodProductList",
            {
                "p_productclscode": product_cls_code,
                "p_startday": start_date,
                "p_endday": end_date,
                "p_itemcategorycode": item_category_code,
                "p_itemcode": item_code,
                "p_kindcode": kind_code,
                "p_productrankcode": rank_code,
                "p_countycode": county_code,
                "p_convert_kg_yn": convert_kg,
            },
        )
        return self._extract_daily_data(raw)

    # ── response normalizers ────────────────────────────────

    @staticmethod
    def _extract_daily_sales(raw: dict) -> list[dict]:
        """dailySalesList 응답에서 가격 항목 리스트를 추출.

        스키마 드리프트 가시성: KAMIS 가 응답 형식을 변경하면 (예: 'price' → 'items')
        과거 코드는 조용히 빈 리스트만 반환해 프론트엔드가 영구히 비어 보였다.
        이제는 형식 이상을 WARNING 으로 남겨 운영자가 즉시 인지할 수 있다.
        """
        try:
            items = raw.get("price", [])
            if isinstance(items, dict):
                items = [items]
            if isinstance(items, list):
                return items
            logger.warning(
                "kamis.extract_daily_sales.unexpected_shape "
                "type=%s top_keys=%s",
                type(items).__name__, list(raw.keys())[:5],
            )
            return []
        except (AttributeError, TypeError) as exc:
            logger.warning(
                "kamis.extract_daily_sales.parse_error err=%s top_keys=%s",
                exc, list(raw.keys())[:5] if isinstance(raw, dict) else "(not dict)",
            )
            return []

    @staticmethod
    def _extract_daily_data(raw: dict) -> dict:
        """periodProductList 응답을 정규화.

        KAMIS 에러 응답: `{"data": ["001"]}` (리스트). 정상 응답: `{"data": {"item": [...]}}`.
        형식 이상은 WARNING 으로 기록 — 스키마 변경 조기 감지.
        """
        empty = {"item_name": "", "kind_name": "", "unit": "", "records": []}
        try:
            price_data = raw.get("data", {})

            # error response: {"data": ["001"]} — 정상 KAMIS 동작이므로 INFO 로 충분
            if isinstance(price_data, list):
                logger.info("kamis.extract_daily_data.error_response code=%s", price_data)
                return empty

            items = price_data.get("item", [])
            if isinstance(items, dict):
                items = [items]
            if not isinstance(items, list):
                logger.warning(
                    "kamis.extract_daily_data.unexpected_items_type type=%s",
                    type(items).__name__,
                )
                return empty

            return {
                "item_name": items[0].get("itemname", "") if items else "",
                "kind_name": items[0].get("kindname", "") if items else "",
                "unit": items[0].get("unit", "") if items else "",
                "records": items,
            }
        except (AttributeError, TypeError, IndexError) as exc:
            logger.warning(
                "kamis.extract_daily_data.parse_error err=%s top_keys=%s",
                exc, list(raw.keys())[:5] if isinstance(raw, dict) else "(not dict)",
            )
            return empty


kamis_service = KamisService(
    api_key=settings.KAMIS_API_KEY,
    cert_id=settings.KAMIS_CERT_ID,
)
