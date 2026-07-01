"""
꿀템연구소 6컷 만화 자동 포스터
──────────────────────────────────────────────────────────────
흐름: Claude → 스크립트 생성 → Pillow 패널 이미지 생성
    → GitHub API 이미지 업로드 → Threads 캐러셀 게시

필요 패키지: pip install anthropic requests pillow
GitHub Actions 워크플로우에서 fonts-nanum 설치 필요
character.png 를 리포 루트에 포함해야 캐릭터 표시됨 (없어도 동작)
"""

import os
import io
import re
import base64
import json
import time
from datetime import datetime
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont
import anthropic

import naver_shopping  # 공용 네이버 쇼핑 모듈

# ─────────────────────────────────────────────────────────────
# 환경변수
# ─────────────────────────────────────────────────────────────
CLAUDE_API_KEY       = os.environ.get("CLAUDE_API_KEY", "")
THREADS_ACCESS_TOKEN = os.environ.get("THREADS_ACCESS_TOKEN", "")
THREADS_USER_ID      = os.environ.get("THREADS_USER_ID", "")
GITHUB_TOKEN         = os.environ.get("GITHUB_TOKEN", "")          # Actions 자동 제공
GITHUB_REPOSITORY    = os.environ.get("GITHUB_REPOSITORY", "")    # "user/repo"
GITHUB_BRANCH        = os.environ.get("GITHUB_REF_NAME", "main")

SCRIPT_DIR           = Path(__file__).parent
CHARACTER_PATH       = SCRIPT_DIR / "character.png"

# ─────────────────────────────────────────────────────────────
# 카테고리
# ─────────────────────────────────────────────────────────────
CATEGORIES = [
    {
        "name":        "자취생 필수템",
        "link":        "https://link.coupang.com/a/e0J5NRuVIy",
        "keyword":     "자취방 혼자 살면서 생기는 불편한 일상",
        "naver_query": "자취 필수템 생활용품",
    },
    {
        "name":        "여름 시즌 아이템",
        "link":        "https://link.coupang.com/a/e0J8XB3t7s",
        "keyword":     "한국 여름 더위로 고통받는 일상",
        "naver_query": "여름 더위 냉감 용품 추천",
    },
    {
        "name":        "주방가전",
        "link":        "https://link.coupang.com/a/e0KcjeIb7I",
        "keyword":     "자취생이 요리하다가 포기하게 되는 순간들",
        "naver_query": "자취 소형 주방가전 추천",
    },
    {
        "name":        "영양제/건강식품",
        "link":        "https://link.coupang.com/a/e0Ke9Db6uy",
        "keyword":     "피곤하고 건강 관리가 힘든 20대 직장인",
        "naver_query": "20대 직장인 영양제 추천",
    },
]

# ─────────────────────────────────────────────────────────────
# 패널 스타일 (컷별 분위기)
# ─────────────────────────────────────────────────────────────
W, H = 1080, 1080

STYLES = {
    "intro": {
        "bg": (255, 252, 230), "header_bg": (255, 204, 0),
        "header_text": (28, 28, 28), "text": (28, 28, 28),
        "bubble_bg": (255, 255, 255), "bubble_border": (255, 204, 0),
        "dot": (240, 230, 200),
    },
    "problem": {
        "bg": (255, 238, 220), "header_bg": (255, 120, 50),
        "header_text": (255, 255, 255), "text": (28, 28, 28),
        "bubble_bg": (255, 255, 255), "bubble_border": (255, 120, 50),
        "dot": (240, 215, 195),
    },
    "crisis": {
        "bg": (240, 225, 255), "header_bg": (130, 65, 210),
        "header_text": (255, 255, 255), "text": (28, 28, 28),
        "bubble_bg": (255, 255, 255), "bubble_border": (130, 65, 210),
        "dot": (220, 205, 245),
    },
    "reveal": {
        "bg": (22, 22, 22), "header_bg": (255, 204, 0),
        "header_text": (22, 22, 22), "text": (255, 255, 255),
        "bubble_bg": (45, 45, 45), "bubble_border": (255, 204, 0),
        "dot": (40, 40, 40),
    },
    "solution": {
        "bg": (220, 255, 232), "header_bg": (35, 185, 90),
        "header_text": (255, 255, 255), "text": (28, 28, 28),
        "bubble_bg": (255, 255, 255), "bubble_border": (35, 185, 90),
        "dot": (195, 240, 210),
    },
    "cta": {
        "bg": (255, 252, 230), "header_bg": (255, 204, 0),
        "header_text": (28, 28, 28), "text": (28, 28, 28),
        "bubble_bg": (255, 255, 255), "bubble_border": (255, 204, 0),
        "dot": (240, 230, 200),
    },
}

PANEL_STYLES = ["intro", "problem", "crisis", "reveal", "solution", "cta"]
SHOW_CHARACTER = {"intro", "reveal", "solution", "cta"}  # 캐릭터 표시할 컷


# ─────────────────────────────────────────────────────────────
# 유틸리티
# ─────────────────────────────────────────────────────────────
_EMOJI_RE = re.compile(
    "[\U0001F300-\U0001F9FF\U00002600-\U000027BF\U0001FA00-\U0001FA9F"
    "\U00002702-\U000027B0️‍]+"
)

def strip_emoji(text: str) -> str:
    return _EMOJI_RE.sub("", text).strip()


def load_fonts() -> dict:
    """Ubuntu (GitHub Actions) 한글 폰트 로드"""
    candidates = [
        "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        "/usr/share/fonts/opentype/nanum/NanumGothicBold.otf",
    ]
    path = next((p for p in candidates if Path(p).exists()), None)
    if path:
        return {
            "header": ImageFont.truetype(path, 40),
            "large":  ImageFont.truetype(path, 52),
            "medium": ImageFont.truetype(path, 38),
            "small":  ImageFont.truetype(path, 26),
            "tiny":   ImageFont.truetype(path, 18),
        }
    # 로컬 테스트 폴백
    default = ImageFont.load_default()
    return {k: default for k in ["header", "large", "medium", "small", "tiny"]}


def wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> list[str]:
    """한국어 포함 텍스트 줄바꿈"""
    lines, current = [], ""
    for token in text.split(" "):
        candidate = (current + " " + token).strip()
        if draw.textbbox((0, 0), candidate, font=font)[2] <= max_w:
            current = candidate
        else:
            if current:
                lines.append(current)
            # token 자체가 max_w 초과 → 글자 단위 분리
            if draw.textbbox((0, 0), token, font=font)[2] > max_w:
                sub = ""
                for ch in token:
                    if draw.textbbox((0, 0), sub + ch, font=font)[2] <= max_w:
                        sub += ch
                    else:
                        lines.append(sub)
                        sub = ch
                current = sub
            else:
                current = token
    if current:
        lines.append(current)
    return lines


def draw_centered(draw: ImageDraw.ImageDraw, text: str, font,
                  cx: int, cy: int, max_w: int, fill, spacing: float = 1.45):
    """여러 줄 중앙 정렬 텍스트"""
    text = strip_emoji(text)
    lines = wrap_text(draw, text, font, max_w)
    if not lines:
        return

    lh = int(draw.textbbox((0, 0), "가나다", font=font)[3] * spacing)
    total_h = lh * len(lines)
    y = cy - total_h // 2

    for line in lines:
        lw = draw.textbbox((0, 0), line, font=font)[2]
        draw.text((cx - lw // 2, y), line, font=font, fill=fill)
        y += lh


def draw_rounded_rect(draw, x1, y1, x2, y2, r, fill=None, outline=None, width=4):
    draw.rounded_rectangle([x1, y1, x2, y2], radius=r,
                            fill=fill, outline=outline, width=width)


# ─────────────────────────────────────────────────────────────
# 1. Claude: 6컷 만화 스크립트 생성
# ─────────────────────────────────────────────────────────────
def generate_script(category: dict, products: list[dict]) -> list[dict]:
    client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
    product_block = naver_shopping.format_for_prompt(products)

    product_section = ""
    if product_block:
        product_section = f"""
오늘 네이버 쇼핑 실시간 인기 상품 데이터 (참고용):
{product_block}

5컷(solution)에서 이 상품들 중 1개를 자연스럽게 힌트로 줘도 됨.
단, 구체적 상품명은 6컷 CTA에서 "댓글에서 확인" 유도만 해.
"""

    prompt = f"""
너는 '꿀템연구소' SNS 6컷 만화 작가야.
주제: {category['keyword']}
{product_section}
해외 틱톡/레딧 밈 유머 감각으로 (과장된 공감, 반전, 절망 then 구원) 6컷 스크립트를 작성해.

컷 구성:
1컷 (intro)  : 독자가 "맞아 이거 나임" 하는 공감 상황
2컷 (problem): 문제가 점점 커지는 상황 (짜증 고조)
3컷 (crisis) : 한계 도달, 최악의 순간 (과장된 절망)
4컷 (reveal) : 꿀템연구소 연구원 등장 / 실험 결과 발표
5컷 (solution): 아이템으로 극적 해결 (before-after, 가격대 살짝 힌트 가능)
6컷 (cta)    : 행복 결말 + 아이템이 뭔지 댓글 링크 유도

규칙:
- top_text: 배경 상황 설명 (15자 이내, 이모지 금지)
- bubble: 캐릭터 대사 (30자 이내, 이모지 금지, 임팩트 있게)
- 해외 밈처럼 과장되게, 20대 공감 언어로
- JSON 배열만 출력 (다른 텍스트 없이)

[
  {{"panel":1,"style":"intro",   "top_text":"배경 상황","bubble":"대사"}},
  {{"panel":2,"style":"problem", "top_text":"배경 상황","bubble":"대사"}},
  {{"panel":3,"style":"crisis",  "top_text":"배경 상황","bubble":"대사"}},
  {{"panel":4,"style":"reveal",  "top_text":"꿀템연구소 등장","bubble":"X개월 실험 결과 공개합니다"}},
  {{"panel":5,"style":"solution","top_text":"배경 상황","bubble":"대사"}},
  {{"panel":6,"style":"cta",     "top_text":"행복한 결말","bubble":"뭔지 궁금하면 댓글 링크 확인"}}
]
"""

    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = msg.content[0].text.strip()

    # JSON 블록 추출
    if "```" in raw:
        for block in raw.split("```"):
            block = block.strip()
            if block.startswith("json"):
                block = block[4:].strip()
            if block.startswith("["):
                raw = block
                break

    panels = json.loads(raw)
    print("  만화 스크립트:")
    for p in panels:
        print(f"  [{p['panel']}] {p['top_text']} / {p['bubble']}")
    return panels


# ─────────────────────────────────────────────────────────────
# 2. Pillow: 패널 이미지 생성
# ─────────────────────────────────────────────────────────────
def create_panel(panel: dict, char_img, fonts: dict,
                 panel_idx: int, total: int) -> bytes:
    style_name = panel.get("style", "intro")
    s = STYLES.get(style_name, STYLES["intro"])

    img = Image.new("RGB", (W, H), s["bg"])
    draw = ImageDraw.Draw(img)

    # 배경 도트 패턴
    for xi in range(35, W, 55):
        for yi in range(35, H, 55):
            draw.ellipse([xi-3, yi-3, xi+3, yi+3], fill=s["dot"])

    # ── 상단 헤더 ──────────────────────────────────────────
    draw.rectangle([0, 0, W, 90], fill=s["header_bg"])
    draw.text((32, 45), "1위 꿀템연구소", font=fonts["header"],
              fill=s["header_text"], anchor="lm")
    draw.text((W - 32, 45), f"{panel_idx} / {total}", font=fonts["small"],
              fill=s["header_text"], anchor="rm")

    # ── 배경 설명 텍스트 (상단 영역) ───────────────────────
    top_text = strip_emoji(panel.get("top_text", ""))
    draw_centered(draw, top_text, fonts["medium"], W // 2, 220, W - 120, s["text"])

    # ── 말풍선 ─────────────────────────────────────────────
    bx1, by1, bx2, by2 = 55, 400, W - 55, 760
    draw_rounded_rect(draw, bx1, by1, bx2, by2, 36,
                      fill=s["bubble_bg"], outline=s["bubble_border"], width=5)

    # 말풍선 꼬리
    tail = [(100, by2), (155, by2), (120, by2 + 55)]
    draw.polygon(tail, fill=s["bubble_bg"])
    draw.line([(100, by2), (120, by2 + 55)], fill=s["bubble_border"], width=5)
    draw.line([(155, by2), (120, by2 + 55)], fill=s["bubble_border"], width=5)

    # 말풍선 내부 텍스트
    bubble_text = panel.get("bubble", "")
    bub_cx = (bx1 + bx2) // 2
    bub_cy = (by1 + by2) // 2
    draw_centered(draw, bubble_text, fonts["large"], bub_cx, bub_cy, bx2 - bx1 - 80, s["text"])

    # ── 캐릭터 이미지 ──────────────────────────────────────
    if char_img and style_name in SHOW_CHARACTER:
        size = 260
        ch = char_img.copy().resize((size, size), Image.LANCZOS)
        px, py = W - size - 12, H - size - 28
        if ch.mode == "RGBA":
            img.paste(ch, (px, py), ch)
        else:
            img.paste(ch, (px, py))

    # ── 하단 공정위 고지 ───────────────────────────────────
    notice_color = (150, 150, 150) if style_name != "reveal" else (100, 100, 100)
    draw.text((W // 2, H - 14),
              "이 포스팅은 쿠팡 파트너스 활동의 일환으로 수수료를 제공받습니다",
              font=fonts["tiny"], fill=notice_color, anchor="mm")

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# ─────────────────────────────────────────────────────────────
# 3. GitHub Contents API: 이미지 업로드
# ─────────────────────────────────────────────────────────────
def upload_image(image_bytes: bytes, filename: str) -> str | None:
    if not GITHUB_TOKEN or not GITHUB_REPOSITORY:
        print("  ⚠️  GITHUB_TOKEN/GITHUB_REPOSITORY 없음")
        return None

    path = f"comic_panels/{filename}"
    api_url = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/contents/{path}"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    b64 = base64.b64encode(image_bytes).decode()
    body: dict = {"message": f"[auto] Add {filename}", "content": b64}

    # 기존 파일 SHA 확인 (덮어쓰기용)
    check = requests.get(api_url, headers=headers, timeout=10)
    if check.status_code == 200:
        body["sha"] = check.json().get("sha", "")

    r = requests.put(api_url, headers=headers, json=body, timeout=20)
    if r.status_code in (200, 201):
        url = (f"https://raw.githubusercontent.com/"
               f"{GITHUB_REPOSITORY}/{GITHUB_BRANCH}/{path}")
        print(f"  ✅ 업로드: {url}")
        return url

    print(f"  ❌ 업로드 실패 {r.status_code}: {r.text[:200]}")
    return None


# ─────────────────────────────────────────────────────────────
# 4. Threads API: 캐러셀 게시
# ─────────────────────────────────────────────────────────────
def _post(payload: dict, timeout: int = 15) -> dict:
    r = requests.post(
        f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads",
        data={**payload, "access_token": THREADS_ACCESS_TOKEN},
        timeout=timeout,
    )
    return r.json()


def _publish(creation_id: str) -> dict:
    r = requests.post(
        f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads_publish",
        data={"creation_id": creation_id, "access_token": THREADS_ACCESS_TOKEN},
        timeout=15,
    )
    return r.json()


def post_carousel(image_urls: list[str], caption: str, link: str) -> str | None:
    # 1. 이미지 컨테이너 개별 생성
    print("  이미지 컨테이너 생성 중...")
    children = []
    for url in image_urls:
        res = _post({"media_type": "IMAGE", "image_url": url, "is_carousel_item": "true"})
        cid = res.get("id")
        if not cid:
            print(f"  ❌ 컨테이너 실패: {res}")
            return None
        children.append(cid)
        print(f"    🖼  {cid}")
        time.sleep(2)

    # 2. 캐러셀 컨테이너
    res = _post({
        "media_type": "CAROUSEL",
        "children": ",".join(children),
        "text": caption,
    })
    carousel_id = res.get("id")
    if not carousel_id:
        print(f"  ❌ 캐러셀 실패: {res}")
        return None
    print(f"  🎠 캐러셀 컨테이너: {carousel_id}")

    time.sleep(3)

    # 3. 발행
    res = _publish(carousel_id)
    post_id = res.get("id")
    if not post_id:
        print(f"  ❌ 발행 실패: {res}")
        return None
    print(f"  ✅ 게시 완료: {post_id}")

    # 4. 댓글에 링크 + 공정위 고지
    time.sleep(2)
    comment = (
        f"👇 아이템 링크\n{link}\n\n"
        "※ 이 포스팅은 쿠팡 파트너스 활동의 일환으로 수수료를 제공받습니다."
    )
    res2 = _post({"media_type": "TEXT", "text": comment, "reply_to_id": post_id})
    cid2 = res2.get("id")
    if cid2:
        time.sleep(2)
        _publish(cid2)
        print("  💬 링크 댓글 완료")

    return post_id


# ─────────────────────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────────────────────
def main():
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    print(f"\n{'='*55}")
    print(f"🎨 꿀템연구소 만화 포스팅 시작: {ts}")
    print(f"{'='*55}\n")

    # 카테고리 (날짜 기반 순환)
    cat = CATEGORIES[datetime.now().day % len(CATEGORIES)]
    print(f"📂 카테고리: {cat['name']}")

    # 폰트
    print("\n🔤 폰트 로드...")
    fonts = load_fonts()

    # 캐릭터 이미지
    char_img = None
    if CHARACTER_PATH.exists():
        char_img = Image.open(CHARACTER_PATH).convert("RGBA")
        print(f"🧑‍🔬 캐릭터 이미지 로드 완료")
    else:
        print("⚠️  character.png 없음 - 캐릭터 없이 진행")

    # 1. 네이버 쇼핑 실제 상품 수집
    print("\n🛍️  네이버 쇼핑 상품 수집 중...")
    products = naver_shopping.fetch_products(cat["naver_query"], count=5)

    # 2. 만화 스크립트 생성
    print("\n✍️  만화 스크립트 생성 중...")
    panels = generate_script(cat, products)

    # 3. 패널 이미지 생성 → GitHub 업로드
    print(f"\n🎨 {len(panels)}컷 이미지 생성 및 업로드...")
    image_urls = []
    for i, panel in enumerate(panels, 1):
        print(f"  패널 {i}/{len(panels)}...")
        img_bytes = create_panel(panel, char_img, fonts, i, len(panels))
        fname = f"{ts}_p{i:02d}.png"
        url = upload_image(img_bytes, fname)
        if url:
            image_urls.append(url)
        time.sleep(1)

    if len(image_urls) < 2:
        print(f"❌ 업로드된 이미지 부족 ({len(image_urls)}개). 종료.")
        return

    # GitHub CDN 캐시 안정화 대기
    print(f"\n⏳ GitHub CDN 안정화 대기 (20초)...")
    time.sleep(20)

    # 3. Threads 캐러셀 게시
    print("\n📤 Threads 캐러셀 게시...")
    caption = "꿀템연구소 실험 결과 공개 \n오른쪽으로 넘겨보세요"
    post_id = post_carousel(image_urls, caption, cat["link"])

    if post_id:
        print(f"\n🎉 만화 포스팅 완료! [{cat['name']}]")
    else:
        print("\n❌ 포스팅 실패")


if __name__ == "__main__":
    main()
