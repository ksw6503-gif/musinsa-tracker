#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
무신사(MUSINSA) 개인용 가격 추적 및 실시간 텔레그램 알림 봇 (폴센트 스타일)
- 실행 방식: python3 tracker.py (또는 GitHub Actions 스케줄러로 30분~1시간 주기 자동 실행)
- 핵심 기능:
  1. 무신사 상품 상세 API / 웹페이지(__NEXT_DATA__ & JSON-LD) 파싱 (브랜드, 상품명, 정가, 판매가, 할인율, 사이즈별 재고)
  2. 이전 상태(state.json / items.json)와 비교하여 3대 핵심 조건 감지:
     - [목표가 도달] 현재 판매가가 내 목표 알림 희망가 이하로 하락했을 때
     - [가격 폭락/역대 최저가] 직전 가격 대비 10% 이상 급락하거나 역대 최저가를 갱신했을 때
     - [사이즈 재입고] 품절이었던 내 타겟 사이즈가 '구매 가능' 상태로 변경되었을 때
  3. 텔레그램 마크다운(Markdown) 푸시 알림 즉시 발송 및 상태 파일 자동 갱신
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional
import requests
from bs4 import BeautifulSoup

# 한국 표준시 (KST, UTC+9)
KST = timezone(timedelta(hours=9))

# 파일 경로 설정
ITEMS_FILE = os.environ.get("ITEMS_FILE", "items.json")
STATE_FILE = os.environ.get("STATE_FILE", "state.json")

# 텔레그램 환경변수 (GitHub Secrets 또는 로컬 환경변수)
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

# 무신사 접근용 브라우저 헤더
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1"
    ),
    "Referer": "https://www.musinsa.com/",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}


def extract_goods_no(url_or_id: str) -> Optional[str]:
    """무신사 상품 URL 또는 품번 문자열에서 숫자형 상품 번호(goodsNo)를 추출합니다."""
    text = str(url_or_id).strip()
    if text.isdigit():
        return text
    match = re.search(r"(?:products|app/goods)/(\d+)", text)
    if match:
        return match.group(1)
    digits = re.findall(r"\d{5,10}", text)
    return digits[0] if digits else None


def fetch_musinsa_product(goods_no: str, target_size: str = "FREE") -> Dict[str, Any]:
    """
    무신사 내부 API 및 상품 페이지 HTML(__NEXT_DATA__)을 순차적으로 조회하여
    상품 정보(브랜드, 상품명, 정가, 할인가, 할인율)와 사이즈별 재고 상태를 반환합니다.
    """
    product_url = f"https://www.musinsa.com/products/{goods_no}"
    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)

    brand = ""
    name = ""
    original_price = 0
    current_price = 0
    discount_rate = 0
    sizes: Dict[str, bool] = {}

    # 1단계: 무신사 상품 상세 API 호출 시도
    api_url = f"https://goods-detail.musinsa.com/api2/goods/{goods_no}"
    try:
        resp = session.get(api_url, timeout=10)
        if resp.status_code == 200:
            payload = resp.json()
            data = payload.get("data") or {}
            brand = (
                data.get("brandInfo", {}).get("brandName")
                or data.get("brand")
                or ""
            )
            name = data.get("goodsNm") or data.get("goodsName") or ""
            price_info = data.get("goodsPrice") or {}
            original_price = int(
                price_info.get("normalPrice")
                or price_info.get("originPrice")
                or 0
            )
            current_price = int(
                price_info.get("salePrice")
                or price_info.get("memberPrice")
                or price_info.get("ImmediateDiscountedPrice")
                or original_price
            )
            discount_rate = int(price_info.get("discountRate") or 0)
            if original_price > 0 and current_price > 0 and discount_rate == 0:
                discount_rate = max(
                    0, round((1 - current_price / original_price) * 100)
                )
    except Exception as e:
        print(f"[WARN] 상세 API 조회 실패 ({goods_no}): {e}")

    # 2단계: 무신사 옵션/재고 API 호출 시도
    option_endpoints = [
        f"https://goods-detail.musinsa.com/api2/goods/{goods_no}/options",
        f"https://goods-detail.musinsa.com/api2/goods/{goods_no}/option",
    ]
    for opt_url in option_endpoints:
        if sizes:
            break
        try:
            opt_resp = session.get(opt_url, timeout=10)
            if opt_resp.status_code == 200:
                opt_json = opt_resp.json()
                opt_data = opt_json.get("data") or {}
                option_items = (
                    opt_data.get("optionItems")
                    or opt_data.get("options")
                    or opt_data.get("basic")
                    or []
                )
                if isinstance(option_items, list):
                    for item in option_items:
                        opt_name = str(
                            item.get("name")
                            or item.get("optionName")
                            or item.get("value")
                            or ""
                        ).strip()
                        if not opt_name:
                            continue
                        sold_out = bool(
                            item.get("isSoldOut")
                            or item.get("soldOut")
                            or item.get("outOfStock")
                            or (item.get("remainQty", 1) == 0)
                        )
                        sizes[opt_name] = not sold_out
        except Exception:
            pass

    # 3단계: API 응답이 불완전할 경우 상품 웹페이지 HTML (__NEXT_DATA__ 및 메타태그) 파싱
    if not name or current_price == 0:
        try:
            page_resp = session.get(product_url, timeout=12)
            if page_resp.status_code == 200:
                soup = BeautifulSoup(page_resp.text, "html.parser")

                # __NEXT_DATA__ 스크립트 파싱
                next_data_tag = soup.find("script", id="__NEXT_DATA__")
                if next_data_tag and next_data_tag.string:
                    next_json = json.loads(next_data_tag.string)
                    page_props = (
                        next_json.get("props", {})
                        .get("pageProps", {})
                        .get("meta", {})
                        .get("data", {})
                    )
                    if not page_props:
                        page_props = next_json.get("props", {}).get(
                            "pageProps", {}
                        )

                    brand = brand or (
                        page_props.get("brandInfo", {}).get("brandName")
                        or page_props.get("brand")
                        or ""
                    )
                    name = name or (
                        page_props.get("goodsNm")
                        or page_props.get("goodsName")
                        or ""
                    )
                    g_price = page_props.get("goodsPrice") or {}
                    if original_price == 0:
                        original_price = int(
                            g_price.get("normalPrice")
                            or g_price.get("originPrice")
                            or 0
                        )
                    if current_price == 0:
                        current_price = int(
                            g_price.get("salePrice")
                            or g_price.get("memberPrice")
                            or original_price
                        )
                    if discount_rate == 0:
                        discount_rate = int(g_price.get("discountRate") or 0)

                # OpenGraph / 메타태그 백업 파싱
                if not name:
                    og_title = soup.find("meta", property="og:title")
                    if og_title and og_title.get("content"):
                        raw_title = og_title["content"]
                        name = re.sub(r"\s*-\s*무신사.*$", "", raw_title).strip()

                if current_price == 0:
                    price_meta = soup.find(
                        "meta", property="product:price:amount"
                    )
                    if price_meta and price_meta.get("content"):
                        current_price = int(
                            re.sub(r"[^\d]", "", price_meta["content"]) or 0
                        )
                        if original_price == 0:
                            original_price = current_price
        except Exception as e:
            print(f"[WARN] HTML 파싱 실패 ({goods_no}): {e}")

    if original_price > 0 and current_price > 0 and discount_rate == 0:
        discount_rate = max(
            0, round((1 - current_price / original_price) * 100)
        )

    # 타겟 사이즈 재고 판별 (대소문자 무시 매칭)
    target_in_stock: Optional[bool] = None
    norm_target = str(target_size).strip().upper()
    if sizes:
        for sz_key, stock_bool in sizes.items():
            if sz_key.strip().upper() == norm_target or norm_target in sz_key.strip().upper():
                target_in_stock = stock_bool
                break

    return {
        "id": goods_no,
        "url": product_url,
        "brand": brand,
        "name": name,
        "original_price": original_price,
        "current_price": current_price,
        "discount_rate": discount_rate,
        "sizes": sizes,
        "in_stock": target_in_stock,
    }


def format_krw(amount: int) -> str:
    """숫자를 천 단위 콤마가 포함된 원화 문자열로 변환합니다."""
    return f"{int(amount):,}원"


def send_telegram_message(text: str) -> bool:
    """텔레그램 봇 API를 통해 마크다운 푸시 알림을 전송합니다."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("\n[INFO] 텔레그램 토큰/채팅방 ID가 설정되지 않아 콘솔 출력으로 대체합니다:")
        print("-" * 60)
        print(text)
        print("-" * 60)
        return False

    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False,
    }
    try:
        resp = requests.post(api_url, json=payload, timeout=10)
        if resp.status_code == 200:
            print("[OK] 텔레그램 푸시 알림 발송 완료")
            return True
        print(f"[ERROR] 텔레그램 발송 실패 ({resp.status_code}): {resp.text}")
        return False
    except Exception as e:
        print(f"[ERROR] 텔레그램 API 요청 예외: {e}")
        return False


def build_alert_message(
    alert_headers: List[str],
    item: Dict[str, Any],
    prev_price: int,
    new_price: int,
    lowest_price: int,
) -> str:
    """시인성이 뛰어난 폴센트 스타일의 텔레그램 알림 메시지를 생성합니다."""
    header_line = "\n".join(alert_headers)
    brand = item.get("brand", "브랜드 미상")
    name = item.get("name", f"상품 #{item.get('id')}")
    goods_no = item.get("id", "")
    target_size = item.get("target_size", "FREE")
    target_price = int(item.get("target_price") or 0)
    original_price = int(item.get("original_price") or new_price)
    discount_rate = int(item.get("discount_rate") or 0)
    in_stock = item.get("in_stock", True)
    url = item.get("url") or f"https://www.musinsa.com/products/{goods_no}"

    # 직전 가격 대비 변동률 계산
    if prev_price > 0 and prev_price != new_price:
        diff_pct = round(((new_price - prev_price) / prev_price) * 100, 1)
        sign = "+" if diff_pct > 0 else ""
        price_trend = f"{format_krw(prev_price)} ➔ *{format_krw(new_price)}* ({sign}{diff_pct}%)"
    else:
        price_trend = f"*{format_krw(new_price)}* (정가 {format_krw(original_price)}, -{discount_rate}%)"

    stock_badge = "🟢 구매 가능" if in_stock else "🔴 품절"
    now_str = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")

    msg = (
        f"{header_line}\n\n"
        f"🏷 *[{brand}] {name}*\n"
        f"• 품번: `{goods_no}` | 타겟 사이즈: *{target_size}* ({stock_badge})\n"
        f"• 가격 변화: {price_trend}\n"
        f"• 내 목표 희망가: {format_krw(target_price)}\n"
        f"• 역대 최저가: {format_krw(lowest_price)}\n"
        f"• 감지 시각: {now_str}\n\n"
        f"👉 [무신사 상품 바로가기]({url})"
    )
    return msg


def evaluate_and_track() -> None:
    """items.json의 상품 목록을 순회하며 가격/재고 변동을 감지하고 알림을 발송합니다."""
    if not os.path.exists(ITEMS_FILE):
        print(f"[ERROR] '{ITEMS_FILE}' 파일을 찾을 수 없습니다.")
        sys.exit(1)

    with open(ITEMS_FILE, "r", encoding="utf-8") as f:
        items: List[Dict[str, Any]] = json.load(f)

    # 이전 상태(state.json) 로드
    prev_state: Dict[str, Dict[str, Any]] = {}
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as sf:
                prev_state = json.load(sf)
        except Exception:
            prev_state = {}

    now_str = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    updated_items: List[Dict[str, Any]] = []
    alerts_sent = 0

    print(f"=== [무신사 가격/재고 추적 시작] 총 {len(items)}개 상품 ({now_str}) ===")

    for item in items:
        goods_no = extract_goods_no(item.get("id") or item.get("url", ""))
        if not goods_no:
            print(f"[SKIP] 유효하지 않은 상품 URL/품번: {item}")
            continue

        target_size = str(item.get("target_size") or "L").strip()
        target_price = int(item.get("target_price") or 0)

        # 이전 기록 불러오기 (state.json 우선, 없으면 items.json 내부 필드 참조)
        history = prev_state.get(goods_no, {})
        prev_price = int(
            history.get("current_price")
            or item.get("last_price")
            or item.get("current_price")
            or 0
        )
        prev_lowest = int(
            history.get("lowest_price")
            or item.get("lowest_price")
            or prev_price
        )
        prev_in_stock = history.get("in_stock", item.get("in_stock", True))

        # 실시간 무신사 데이터 조회
        fetched = fetch_musinsa_product(goods_no, target_size=target_size)

        # 네트워크 차단 또는 오프라인 환경일 경우 기존 items.json의 데이터 폴백 유지
        new_price = int(fetched.get("current_price") or item.get("current_price") or 0)
        original_price = int(
            fetched.get("original_price") or item.get("original_price") or new_price
        )
        discount_rate = int(
            fetched.get("discount_rate") or item.get("discount_rate") or 0
        )
        brand = fetched.get("brand") or item.get("brand") or "MUSINSA"
        name = fetched.get("name") or item.get("name") or f"무신사 상품 #{goods_no}"
        sizes = fetched.get("sizes") or item.get("sizes") or {}
        in_stock = (
            fetched["in_stock"]
            if fetched.get("in_stock") is not None
            else bool(item.get("in_stock", True))
        )

        # 역대 최저가 계산
        if prev_lowest > 0 and new_price > 0:
            lowest_price = min(prev_lowest, new_price)
        else:
            lowest_price = new_price or prev_lowest

        # --- 폴센트 3대 핵심 알림 조건 판별 ---
        alert_headers: List[str] = []

        # 조건 1: [목표가 도달] 현재가가 목표 알림 희망가 이하로 떨어졌을 때
        # (단, 이미 목표가 이하로 알림을 보냈던 동일 가격이면 중복 발송 방지)
        already_notified_target = history.get("notified_target_price") == new_price
        if (
            target_price > 0
            and new_price > 0
            and new_price <= target_price
            and not already_notified_target
        ):
            alert_headers.append(
                f"🎯 *[목표가 도달]* 희망가({format_krw(target_price)}) 이하 달성!"
            )

        # 조건 2: [가격 폭락 (급하락) / 역대 최저가 갱신]
        if prev_price > 0 and new_price > 0 and new_price < prev_price:
            drop_ratio = (prev_price - new_price) / prev_price
            if drop_ratio >= 0.10:
                drop_pct = round(drop_ratio * 100, 1)
                alert_headers.append(
                    f"🚨 *[무신사 가격 폭락]* 직전가 대비 -{drop_pct}% 급하락!"
                )
            elif prev_lowest > 0 and new_price < prev_lowest:
                alert_headers.append(
                    f"🔥 *[역대 최저가 갱신]* 기존 최저가({format_krw(prev_lowest)}) 돌파!"
                )

        # 조건 3: [사이즈 재입고] 기존 '품절(False)' ➔ 현재 '구매 가능(True)'
        if prev_in_stock is False and in_stock is True:
            alert_headers.append(
                f"🔔 *[원하던 {target_size}사이즈 재입고]* 품절 해제! 즉시 구매 가능"
            )

        # 아이템 정보 업데이트
        updated_item = {
            "id": goods_no,
            "url": f"https://www.musinsa.com/products/{goods_no}",
            "brand": brand,
            "name": name,
            "target_size": target_size,
            "target_price": target_price,
            "current_price": new_price,
            "original_price": original_price,
            "lowest_price": lowest_price,
            "last_price": prev_price if prev_price != new_price else item.get("last_price", new_price),
            "discount_rate": discount_rate,
            "in_stock": in_stock,
            "sizes": sizes,
            "updated_at": now_str,
        }
        updated_items.append(updated_item)

        # 알림 조건이 하나라도 충족되면 텔레그램 발송
        if alert_headers:
            msg = build_alert_message(
                alert_headers, updated_item, prev_price, new_price, lowest_price
            )
            send_telegram_message(msg)
            alerts_sent += 1

        # 상태 저장용 딕셔너리 갱신
        prev_state[goods_no] = {
            "current_price": new_price,
            "lowest_price": lowest_price,
            "in_stock": in_stock,
            "notified_target_price": (
                new_price
                if (target_price > 0 and new_price <= target_price)
                else history.get("notified_target_price")
            ),
            "updated_at": now_str,
        }

        time.sleep(0.5)

    # items.json 및 state.json 저장
    with open(ITEMS_FILE, "w", encoding="utf-8") as f:
        json.dump(updated_items, f, ensure_ascii=False, indent=2)

    with open(STATE_FILE, "w", encoding="utf-8") as sf:
        json.dump(prev_state, sf, ensure_ascii=False, indent=2)

    print(
        f"=== [추적 완료] 업데이트된 상품: {len(updated_items)}개 | 발송된 알림: {alerts_sent}건 ==="
    )


if __name__ == "__main__":
    evaluate_and_track()
