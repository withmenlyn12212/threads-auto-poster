"""
꿀템연구소 텍스트 자동 포스터 v3
──────────────────────────────────────────────────────────────
네이버 쇼핑 API로 실제 상품 데이터를 수집한 뒤
Claude Sonnet이 짧은 생활 후기체로 Threads 게시글 생성

사용법:
  python threads_auto_poster.py            # 실제 게시
  python threads_auto_poster.py --dry-run  # 콘솔 출력만 (게시 안 함)

필요 패키지: pip install anthropic requests
"""

import os
import sys
import time
import random
import hashlib
import requests
from datetime import datetime
import anthropic

import naver_shopping

# ── dry-run 플래그 ────────────────────────────────────────────
DRY_RUN = "--dry-run" in sys.argv

# ── 환경 변수 (GitHub Secrets에 등록) ────────────────────────
CLAUDE_API_KEY       = os.environ.get("CLAUDE_API_KEY", "")
THREADS_ACCESS_TOKEN = os.environ.get("THREADS_ACCESS_TOKEN", "")
THREADS_USER_ID      = os.environ.get("THREADS_USER_ID", "")

# ── CTA 문구 풀 (매 게시마다 랜덤) ───────────────────────────
CTA_PHRASES = [
    "제품은 댓글에 남겨둘게요.",
    "궁금한 분들은 댓글 확인해보세요.",
    "제품 링크는 댓글에 있어요.",
    "자세한 건 댓글에 적어뒀어요.",
    "댓글에서 제품 확인하세요.",
]

DISCLOSURE = "쿠팡파트너스 활동으로 수수료를 받을 수 있어요."

# ── 카테고리 + 쿠팡 파트너스 링크 ────────────────────────────
CATEGORIES = [
    {
        "name":        "자취생 필수템",
        "link":        "https://link.coupang.com/a/e0J5NRuVIy",
        "naver_query": "자취 필수템 생활용품",
        "situation": [
            "자취방 책상 위가 맨날 꽉 찬 상황",
            "냉장고 정리가 매번 엉망인 상황",
            "콘센트 자리가 부족해서 멀티탭을 멀티탭에 꽂는 상황",
        ],
    },
    {
        "name":        "여름 시즌 아이템",
        "link":        "https://link.coupang.com/a/e0J8XB3t7s",
        "naver_query": "여름 더위 냉감 용품",
        "situation": [
            "에어컨 켜기엔 좀 애매하고 선풍기만으론 부족한 상황",
            "잠잘 때 더워서 새벽에 계속 깨는 상황",
            "실외기 없어서 에어컨 못 다는 자취방 상황",
        ],
    },
    {
        "name":        "주방가전",
        "link":        "https://link.coupang.com/a/e0KcjeIb7I",
        "naver_query": "자취 소형 주방가전",
        "situation": [
            "편의점 도시락 매일 먹다가 질린 상황",
            "요리하기는 귀찮고 배달비는 아까운 상황",
            "주방이 작아서 큰 가전을 못 두는 상황",
        ],
    },
    {
        "name":        "영양제/건강식품",
        "link":        "https://link.coupang.com/a/e0Ke9Db6uy",
        "naver_query": "20대 직장인 영양제",
        "situation": [
            "피곤한데 뭘 먹어야 할지 모르는 상황",
            "영양제 사려고 약국 갔다가 뭐가 뭔지 몰라서 그냥 나온 상황",
            "챙겨 먹다 자꾸 까먹어서 결국 반도 못 먹은 상황",
        ],
    },
]


# ── 시간 기반 상황 선택 ───────────────────────────────────────
def pick_situation(category: dict) -> str:
    seed = datetime.now().strftime("%Y%m%d%H")
    idx = int(hashlib.md5(seed.encode()).hexdigest(), 16) % len(category["situation"])
    return category["situation"][idx]


# ── Claude: 게시글 생성 ───────────────────────────────────────
def generate_post(category: dict, products: list[dict]) -> str:
    client   = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
    situation = pick_situation(category)
    product_block = naver_shopping.format_for_prompt(products)

    product_section = ""
    if product_block:
        product_section = f"""
아래 상품 중 1~2개를 자연스럽게 글에 녹여줘. 상품명 그대로 언급해도 됨.
{product_block}
"""

    prompt = f"""
너는 꿀템연구소 운영자야. Threads에 짧은 생활 후기를 올리는 계정.
광고처럼 보이지 않고 친구가 가볍게 올린 발견 같은 느낌이 핵심.

상황: {situation}
카테고리: {category['name']}
{product_section}

[글쓰기 규칙]
- 전체 120~200자 (이 범위 꼭 지켜)
- 2~3문단, 한 문단 1~2줄
- 첫 줄: 공감되는 일상 상황 하나 (설명 NO, 상황 묘사만)
- 중간: 발견한 것 담백하게 ("~ 놔봤는데" / "~ 써봤더니")
- 가격 언급 시 "X만원대" 정도로만 (강조 금지)
- 말투: 20~30대 혼자 중얼거리는 톤. 반말.
- 마무리 문장: 쓰지 말 것 (CTA는 따로 붙임)

[절대 금지 단어]
추천, 강추, 클릭, 지금 바로, 연구 결과, 데이터 있음, 실험 완료, 반박불가, 효과 있음, 필수, 강력

[좋은 예시]
자취방 책상 위가 맨날 꽉 차서
작은 수납함 하나 놨는데 생각보다 편하네요.

충전기랑 립밤만 빠져도 책상 느낌이 좀 달라짐.

본문만 출력해. 설명 붙이지 마.
"""

    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}]
    )
    return msg.content[0].text.strip()


# ── Threads: 본문 게시 ────────────────────────────────────────
def post_to_threads(text: str) -> str | None:
    try:
        res = requests.post(
            f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads",
            data={"media_type": "TEXT", "text": text,
                  "access_token": THREADS_ACCESS_TOKEN},
            timeout=15,
        )
        cid = res.json().get("id")
        if not cid:
            print(f"  ❌ 컨테이너 생성 실패: {res.json()}")
            return None
        time.sleep(3)
        pub = requests.post(
            f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads_publish",
            data={"creation_id": cid, "access_token": THREADS_ACCESS_TOKEN},
            timeout=15,
        )
        pid = pub.json().get("id")
        if pid:
            print(f"  ✅ 게시 완료 | {pid}")
            return pid
        print(f"  ❌ 게시 실패: {pub.json()}")
        return None
    except Exception as e:
        print(f"  ❌ Threads 오류: {e}")
        return None


# ── Threads: 댓글(쿠팡 링크) ─────────────────────────────────
def post_comment(post_id: str, link: str) -> bool:
    comment = (
        f"👇 상품 링크\n{link}\n\n"
        "※ 이 포스팅은 쿠팡 파트너스 활동의 일환으로,\n"
        "이에 따른 일정액의 수수료를 제공받습니다."
    )
    try:
        res = requests.post(
            f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads",
            data={"media_type": "TEXT", "text": comment,
                  "reply_to_id": post_id, "access_token": THREADS_ACCESS_TOKEN},
            timeout=15,
        )
        cid = res.json().get("id")
        if not cid:
            return False
        time.sleep(2)
        pub = requests.post(
            f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads_publish",
            data={"creation_id": cid, "access_token": THREADS_ACCESS_TOKEN},
            timeout=15,
        )
        ok = "id" in pub.json()
        if ok:
            print("  ✅ 댓글(링크) 게시 완료")
        return ok
    except Exception as e:
        print(f"  ❌ 댓글 오류: {e}")
        return False


# ── 메인 ──────────────────────────────────────────────────────
def main():
    mode = "🔍 DRY-RUN" if DRY_RUN else "🚀 실제 게시"
    print(f"\n{'='*52}")
    print(f"🤖 꿀템연구소 텍스트 포스팅 [{mode}]")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*52}\n")

    now = datetime.now()
    cat = CATEGORIES[(now.day + now.hour // 8) % len(CATEGORIES)]
    print(f"📂 카테고리: {cat['name']} (날짜:{now.day} 시간대:{now.hour//8})")

    print(f"\n🛍️  네이버 쇼핑 수집 중... [{cat['naver_query']}]")
    products = naver_shopping.fetch_products(cat["naver_query"], count=5)
    if products:
        print(f"  ✅ {len(products)}개 수집")

    print("\n✍️  글 생성 중...")
    body = generate_post(cat, products)

    # CTA + 고지 결합
    cta = random.choice(CTA_PHRASES)
    post_text = f"{body}\n\n{cta}\n{DISCLOSURE}"

    print(f"\n📝 최종 게시글:")
    print(f"{'─'*40}")
    print(post_text)
    print(f"{'─'*40}")
    print(f"  글자 수: {len(post_text)}자")

    if DRY_RUN:
        print("\n✅ [DRY-RUN] 게시 생략. 위 내용을 확인하세요.")
        return

    print("\n📤 Threads 게시 중...")
    post_id = post_to_threads(post_text)
    if not post_id:
        print("❌ 게시 실패. 종료.")
        return

    time.sleep(2)
    print("\n💬 링크 댓글 추가 중...")
    post_comment(post_id, cat["link"])

    print(f"\n🎉 완료! [{cat['name']}]")


if __name__ == "__main__":
    main()
