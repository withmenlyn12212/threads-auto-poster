"""
네이버 쇼핑 API 모듈
─────────────────────────────────────────────────
카테고리별 실제 인기 상품 데이터를 수집해서
Claude 프롬프트에 전달하기 위한 공용 모듈

필요 환경변수:
  NAVER_CLIENT_ID      - 네이버 개발자 앱 Client ID
  NAVER_CLIENT_SECRET  - 네이버 개발자 앱 Client Secret

등록 방법: https://developers.naver.com
  → 애플리케이션 등록 → 검색 API → 쇼핑 체크
"""

import os
import re
import requests

NAVER_CLIENT_ID     = os.environ.get("NAVER_CLIENT_ID", "")
NAVER_CLIENT_SECRET = os.environ.get("NAVER_CLIENT_SECRET", "")
SHOP_URL            = "https://openapi.naver.com/v1/search/shop.json"


def _clean(text: str) -> str:
    """HTML 태그 제거"""
    return re.sub(r"<[^>]+>", "", text).strip()


def fetch_products(query: str, count: int = 5) -> list[dict]:
    """
    네이버 쇼핑에서 인기 상품 검색

    Returns:
        [{"name": str, "price": str, "brand": str, "mall": str}, ...]
        API 키 없거나 실패 시 빈 리스트 반환 (graceful fallback)
    """
    if not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET:
        print("  ⚠️  NAVER API 키 없음 - 상품 검색 건너뜀")
        return []

    headers = {
        "X-Naver-Client-Id":     NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
    }
    params = {
        "query":   query,
        "display": count,
        "sort":    "sim",   # sim = 유사도(인기) 순
    }

    try:
        r = requests.get(SHOP_URL, headers=headers, params=params, timeout=10)

        if r.status_code == 401:
            print("  ⚠️  네이버 API 인증 실패 - Client ID/Secret 확인 필요")
            return []
        if r.status_code != 200:
            print(f"  ⚠️  네이버 API 오류 {r.status_code}")
            return []

        items = r.json().get("items", [])
        products = []
        for item in items:
            # 가격 포맷
            try:
                price_str = f"{int(item.get('lprice', 0)):,}원"
            except (ValueError, TypeError):
                price_str = "가격미상"

            # 상품명 정리 (HTML 태그 + 30자 제한)
            name = _clean(item.get("title", ""))[:35]
            brand = _clean(item.get("brand", ""))

            products.append({
                "name":  name,
                "price": price_str,
                "brand": brand,
                "mall":  item.get("mallName", ""),
            })

        print(f"  🛍️  [{query}] → {len(products)}개 상품 수집")
        return products

    except requests.exceptions.Timeout:
        print("  ⚠️  네이버 API 타임아웃")
        return []
    except Exception as e:
        print(f"  ⚠️  네이버 API 오류: {e}")
        return []


def format_for_prompt(products: list[dict]) -> str:
    """
    Claude 프롬프트에 삽입할 상품 데이터 텍스트 생성

    Example output:
        [네이버 쇼핑 실제 인기 상품]
        1. 무선 청소기 소형 (다이슨) - 89,000원
        2. 접이식 건조대 - 23,500원
        ...
    """
    if not products:
        return ""

    lines = ["[네이버 쇼핑 실제 인기 상품]"]
    for i, p in enumerate(products, 1):
        brand_tag = f" ({p['brand']})" if p["brand"] else ""
        lines.append(f"{i}. {p['name']}{brand_tag} - {p['price']}")

    return "\n".join(lines)
