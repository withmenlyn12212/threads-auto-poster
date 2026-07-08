"""
꿀템연구소 웹툰 자동 포스터 v3
──────────────────────────────────────────────────────────────
1080×1350 단일 이미지, 6컷 웹툰 레이아웃
- 컷당 짧은 대사 (8~16자)
- 검은 패널 제거, 밝은 대비
- 캐릭터 2~3컷만 사용
- dry-run 모드 지원

사용법:
  python comic_poster.py            # 실제 게시
  python comic_poster.py --dry-run  # 로컬 확인만 (업로드/게시 안 함)

필요 패키지: pip install anthropic requests pillow
"""

import os
import re
import sys
import json
import time
import base64
import random
import textwrap
import requests
from pathlib import Path
from datetime import datetime
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
import anthropic
import naver_shopping

# ── dry-run 플래그 ────────────────────────────────────────────
DRY_RUN = "--dry-run" in sys.argv

# ── 경로 ──────────────────────────────────────────────────────
SCRIPT_DIR     = Path(__file__).parent
CHARACTER_PATH = SCRIPT_DIR / "character.png"

# ── 환경 변수 ─────────────────────────────────────────────────
CLAUDE_API_KEY       = os.environ.get("CLAUDE_API_KEY", "")
THREADS_ACCESS_TOKEN = os.environ.get("THREADS_ACCESS_TOKEN", "")
THREADS_USER_ID      = os.environ.get("THREADS_USER_ID", "")
GITHUB_TOKEN         = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO          = "a01030589992-dotcom/threads-auto-poster"
GITHUB_BRANCH        = "main"

# ── 캔버스 ────────────────────────────────────────────────────
W      = 1080
H      = 1350   # 4:5 비율
MARGIN = 14
GUTTER = 8
BG     = "#F0F0F0"

# ── 패널 레이아웃 (6컷) ───────────────────────────────────────
# 구성: [전체폭] / [반반] / [전체폭] / [반반] + 로고바(작게)
HALF_W = (W - MARGIN * 2 - GUTTER) // 2   # 525px

ROW1_H = 285   # 인트로 (전체폭) — 크게
ROW2_H = 310   # 공감/위기 (반반)
ROW3_H = 265   # 반전 (전체폭)
ROW4_H = 275   # 상품/CTA (반반)
LOGO_H = 80    # 하단 로고바 (작게)

Y1    = MARGIN
Y2    = Y1 + ROW1_H + GUTTER
Y3    = Y2 + ROW2_H + GUTTER
Y4    = Y3 + ROW3_H + GUTTER
YLOGO = Y4 + ROW4_H + GUTTER

PANEL_RECTS = [
    (MARGIN,                   Y1, W - MARGIN * 2, ROW1_H),  # 0 intro   전체폭
    (MARGIN,                   Y2, HALF_W,          ROW2_H),  # 1 problem 좌
    (MARGIN + HALF_W + GUTTER, Y2, HALF_W,          ROW2_H),  # 2 crisis  우
    (MARGIN,                   Y3, W - MARGIN * 2, ROW3_H),  # 3 reveal  전체폭
    (MARGIN,                   Y4, HALF_W,          ROW4_H),  # 4 solution 좌
    (MARGIN + HALF_W + GUTTER, Y4, HALF_W,          ROW4_H),  # 5 cta     우
]

# 패널 배경색 — 모두 밝은 파스텔 (검정 계열 없음)
PANEL_BG = [
    "#FFFDE7",   # 0 intro    연노랑
    "#FFF3E0",   # 1 problem  연주황
    "#FCE4EC",   # 2 crisis   연분홍 (기존 보라→분홍, 대비 높게)
    "#E8F5E9",   # 3 reveal   연초록
    "#E3F2FD",   # 4 solution 연파랑
    "#FFF9C4",   # 5 cta      밝은노랑
]

# 캐릭터 표시: 0,1,3,5 (4컷 → 2~3컷으로 줄임: 0,3,5만)
SHOW_CHAR   = [True,  False, False, True,  False, True]
# 말풍선: 캐릭터 있는 컷에만
SHOW_BUBBLE = [True,  True,  False, True,  False, True]

BORDER_CLR = "#222222"
BUBBLE_CLR = "#FFFFFF"
TEXT_CLR   = "#1A1A1A"
CRISIS_CLR = "#C62828"   # 위기 컷 텍스트 (빨강, 배경은 분홍으로 충분히 대비)

# ── CTA 문구 풀 (랜덤) ────────────────────────────────────────
CTA_PHRASES = [
    "제품은 댓글에 남겨둘게요.",
    "궁금한 분들은 댓글 확인해보세요.",
    "제품 링크는 댓글에 있어요.",
    "자세한 건 댓글에 적어뒀어요.",
    "댓글에서 제품 확인하세요.",
]

# ── 카테고리 ──────────────────────────────────────────────────
CATEGORIES = [
    {
        "name":        "자취생 필수템",
        "link":        "https://link.coupang.com/a/e0J5NRuVIy",
        "naver_query": "자취 필수템 생활용품",
    },
    {
        "name":        "여름 시즌 아이템",
        "link":        "https://link.coupang.com/a/e0J8XB3t7s",
        "naver_query": "여름 더위 냉감 용품",
    },
    {
        "name":        "주방가전",
        "link":        "https://link.coupang.com/a/e0KcjeIb7I",
        "naver_query": "자취 소형 주방가전",
    },
    {
        "name":        "영양제/건강식품",
        "link":        "https://link.coupang.com/a/e0Ke9Db6uy",
        "naver_query": "20대 직장인 영양제",
    },
]


# ── 폰트 로드 ─────────────────────────────────────────────────
def load_fonts():
    bold_cands = [
        "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    ]
    reg_cands = [
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
    ]
    bold = next((p for p in bold_cands if Path(p).exists()), None)
    reg  = next((p for p in reg_cands  if Path(p).exists()), None)

    def fnt(path, size):
        return ImageFont.truetype(path, size) if path else ImageFont.load_default()

    return {
        "bubble":    fnt(reg,  36),   # 말풍선 — 크게
        "narration": fnt(bold, 38),   # 나레이션 — 크고 굵게
        "small":     fnt(reg,  22),
        "logo":      fnt(bold, 24),
        "panel_num": fnt(bold, 18),
        "solution":  fnt(reg,  32),   # 상품 소개 컷
    }


# ── Claude: 웹툰 스크립트 생성 ────────────────────────────────
def generate_script(category: dict, products: list[dict]) -> dict:
    client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
    product_block = naver_shopping.format_for_prompt(products)

    prompt = f"""
너는 꿀템연구소 6컷 웹툰 작가야. 짧고 자연스러운 생활 발견 만화를 써.

카테고리: {category['name']}
{f"실제 상품 데이터:{chr(10)}{product_block}" if product_block else ""}

[핵심 규칙]
- 각 컷 대사: 8~16자 권장, 절대 22자 초과 금지
- 말투: 20대가 혼자 중얼거리는 느낌
- 과장 금지. 소소하고 구체적인 상황으로
- 광고어 절대 금지: 추천, 강추, 클릭, 지금 바로, 연구 결과, 데이터 있음, 실험 완료, 반박불가

[컷 흐름]
1(intro): 일상 상황 한 줄 — "책상이 또 터짐"
2(problem): 문제 인식 — "버릴 건 없는데?"
3(crisis): 핵심 팩트 한 방 — "문제는 자리였음" (짧고 강하게)
4(reveal): 해결 발견 — "작은 수납함 둬봄"
5(solution): 상품명+가격 담백하게 — "멀티수납함 만원대"
6(cta): 소감 한 줄 — "오... 좀 비네"

[caption 규칙]
- 120~200자
- 2~3문단, 한 문단 2줄 이내
- 실제 경험한 것처럼 구체적 상황 포함
- 마지막 줄: CTA 자연스럽게 + "쿠팡파트너스 활동으로 수수료를 받을 수 있어요."
- 금지: 긴 설명, 가격 강조, 효능 단정

JSON만 출력. 설명 없이.

{{
  "title": "제목 15자이내",
  "panels": [
    {{"type": "intro",    "text": "8~16자", "has_char": true}},
    {{"type": "problem",  "text": "8~16자", "has_char": false}},
    {{"type": "crisis",   "text": "8~14자", "has_char": false}},
    {{"type": "reveal",   "text": "8~16자", "has_char": true}},
    {{"type": "solution", "text": "상품명+가격 16자이내", "has_char": false}},
    {{"type": "cta",      "text": "8~14자", "has_char": true}}
  ],
  "caption": "120~200자 자연스러운 생활 후기",
  "product_highlight": "핵심상품명 12자이내"
}}
"""
    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = msg.content[0].text.strip()
    m   = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        raise ValueError(f"JSON 파싱 실패:\n{raw[:300]}")
    return json.loads(m.group())


# ── 말풍선 그리기 ─────────────────────────────────────────────
def draw_bubble(draw, text, px, py, pw, ph, font, has_char_below=True):
    pad   = 12
    bub_h = int(ph * 0.38) if has_char_below else int(ph * 0.55)
    bx1, by1 = px + pad, py + pad
    bx2, by2 = px + pw - pad, py + bub_h

    draw.rounded_rectangle([bx1, by1, bx2, by2],
                            radius=16, fill=BUBBLE_CLR,
                            outline=BORDER_CLR, width=2)
    if has_char_below:
        cx = (bx1 + bx2) // 2
        ty = by2
        draw.polygon([(cx-12, ty), (cx+12, ty), (cx, ty+18)], fill=BUBBLE_CLR)
        draw.line([(cx-12, ty), (cx, ty+18)], fill=BORDER_CLR, width=2)
        draw.line([(cx+12, ty), (cx, ty+18)], fill=BORDER_CLR, width=2)

    draw.text(((bx1+bx2)//2, (by1+by2)//2), text,
              font=font, fill=TEXT_CLR, anchor="mm", align="center")


# ── 나레이션 텍스트 (crisis/solution 컷) ─────────────────────
def draw_center_text(draw, text, px, py, pw, ph, font, color=TEXT_CLR):
    draw.text((px + pw // 2, py + ph // 2), text,
              font=font, fill=color, anchor="mm", align="center")


# ── 웹툰 이미지 생성 ──────────────────────────────────────────
def create_webtoon_image(script: dict, category: dict) -> bytes:
    fonts = load_fonts()

    char_img = None
    if CHARACTER_PATH.exists():
        try:
            char_img = Image.open(CHARACTER_PATH).convert("RGBA")
            print(f"  ✅ 캐릭터 로드: {char_img.size}")
        except Exception as e:
            print(f"  ⚠️  캐릭터 로드 실패: {e}")

    canvas = Image.new("RGB", (W, H), BG)
    draw   = ImageDraw.Draw(canvas)
    panels = script.get("panels", [])

    for idx, (px, py, pw, ph) in enumerate(PANEL_RECTS):
        if idx >= len(panels):
            break
        panel = panels[idx]
        text  = panel.get("text", "")
        ptype = panel.get("type", "")

        # ① 패널 배경 + 테두리
        draw.rectangle([px, py, px+pw, py+ph],
                       fill=PANEL_BG[idx], outline=BORDER_CLR, width=3)

        # ② 컷 번호 (작은 원)
        nr = 14
        nx, ny = px + 8, py + 8
        draw.ellipse([nx, ny, nx+nr*2, ny+nr*2], fill=BORDER_CLR)
        draw.text((nx+nr, ny+nr), str(idx+1),
                  font=fonts["panel_num"], fill="#FFF", anchor="mm")

        # ③ 캐릭터 (지정된 컷만)
        if char_img and SHOW_CHAR[idx]:
            char_h = int(ph * 0.55)
            ratio  = char_img.width / char_img.height
            char_w = min(int(char_h * ratio), pw - 16)
            c      = char_img.resize((char_w, char_h), Image.LANCZOS)
            cx_pos = px + (pw - char_w) // 2
            cy_pos = py + ph - char_h - 4
            canvas.paste(c, (cx_pos, cy_pos), c)

        if not text:
            continue

        # ④ 텍스트 렌더링
        if SHOW_BUBBLE[idx]:
            has_below = char_img is not None and SHOW_CHAR[idx]
            draw_bubble(draw, text, px, py, pw, ph,
                        fonts["bubble"], has_char_below=has_below)
        elif ptype == "crisis":
            # 위기 컷: 굵고 빨간 텍스트, 밝은 배경 그대로
            draw_center_text(draw, text, px, py, pw, ph,
                             fonts["narration"], color=CRISIS_CLR)
        elif ptype == "solution":
            # 상품 컷: 중앙 텍스트
            draw_center_text(draw, text, px, py, pw, ph,
                             fonts["solution"], color=TEXT_CLR)
        else:
            draw_center_text(draw, text, px, py, pw, ph,
                             fonts["narration"], color=TEXT_CLR)

    # ── 하단 로고바 (작게) ───────────────────────────────────
    lx1, ly1 = MARGIN, YLOGO
    lx2, ly2 = W - MARGIN, H - MARGIN
    draw.rectangle([lx1, ly1, lx2, ly2],
                   fill="#FFF8E1", outline=BORDER_CLR, width=2)
    logo_cy = (ly1 + ly2) // 2
    product = script.get("product_highlight", "")
    logo_text = f"🍯 꿀템연구소  |  {product}" if product else "🍯 꿀템연구소"
    draw.text((W//2, logo_cy), logo_text,
              font=fonts["logo"], fill="#5D4037", anchor="mm")

    buf = BytesIO()
    canvas.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# ── GitHub: 이미지 업로드 ─────────────────────────────────────
def upload_image(image_bytes: bytes, filename: str) -> str:
    api_url = (f"https://api.github.com/repos/{GITHUB_REPO}"
               f"/contents/images/{filename}")
    headers = {"Authorization": f"token {GITHUB_TOKEN}",
               "Accept": "application/vnd.github.v3+json"}
    sha = None
    try:
        r = requests.get(api_url, headers=headers, timeout=10)
        if r.status_code == 200:
            sha = r.json().get("sha")
    except Exception:
        pass

    payload: dict = {
        "message": f"webtoon: {filename}",
        "content": base64.b64encode(image_bytes).decode(),
        "branch":  GITHUB_BRANCH,
    }
    if sha:
        payload["sha"] = sha

    r = requests.put(api_url, headers=headers, json=payload, timeout=30)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"GitHub 업로드 실패: {r.status_code} {r.text[:200]}")

    raw_url = (f"https://raw.githubusercontent.com"
               f"/{GITHUB_REPO}/{GITHUB_BRANCH}/images/{filename}")
    print(f"  ✅ GitHub 업로드: {raw_url}")
    return raw_url


# ── Threads: 이미지 포스팅 ───────────────────────────────────
def post_image_to_threads(image_url: str, caption: str) -> str | None:
    res = requests.post(
        f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads",
        data={"media_type": "IMAGE", "image_url": image_url,
              "text": caption, "access_token": THREADS_ACCESS_TOKEN},
        timeout=20,
    )
    cid = res.json().get("id")
    if not cid:
        print(f"  ❌ 컨테이너 생성 실패: {res.json()}")
        return None
    print(f"  ✅ 컨테이너 생성: {cid}")
    time.sleep(5)

    pub = requests.post(
        f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads_publish",
        data={"creation_id": cid, "access_token": THREADS_ACCESS_TOKEN},
        timeout=20,
    )
    pid = pub.json().get("id")
    if not pid:
        print(f"  ❌ 게시 실패: {pub.json()}")
        return None
    print(f"  ✅ 게시 완료: {pid}")
    return pid


# ── Threads: 댓글(쿠팡 링크) ─────────────────────────────────
def post_link_comment(post_id: str, link: str) -> bool:
    comment = (f"👇 상품 링크\n{link}\n\n"
               "※ 이 포스팅은 쿠팡 파트너스 활동의 일환으로,\n"
               "이에 따른 일정액의 수수료를 제공받습니다.")
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
        print("  ✅ 링크 댓글 게시 완료")
    return ok


# ── 메인 ──────────────────────────────────────────────────────
def main():
    mode = "🔍 DRY-RUN (로컬 확인)" if DRY_RUN else "🚀 실제 게시"
    print(f"\n{'='*52}")
    print(f"🎨 꿀템연구소 웹툰 포스터 [{mode}]")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*52}")

    now = datetime.now()
    cat = CATEGORIES[(now.day + now.hour // 8) % len(CATEGORIES)]
    print(f"\n📂 카테고리: {cat['name']}")

    # 1. 네이버 쇼핑
    print(f"\n🛍️  네이버 쇼핑 수집 중... [{cat['naver_query']}]")
    products = naver_shopping.fetch_products(cat["naver_query"], count=5)
    if products:
        print(f"  ✅ {len(products)}개 수집:")
        for p in products:
            print(f"     ✔ {p['name']} — {p['price']}")
    else:
        print("  ⚠️  미수집 — 기본 진행")

    # 2. 스크립트 생성
    print("\n✍️  스크립트 생성 중...")
    script = generate_script(cat, products)
    print(f"  제목: {script.get('title')}")
    print(f"  상품: {script.get('product_highlight')}")
    print("  컷 내용:")
    for i, p in enumerate(script.get("panels", []), 1):
        print(f"    [{i}] {p.get('type',''):10s}| {p.get('text','')}")

    # 3. 이미지 생성
    print("\n🖼️  이미지 생성 중...")
    image_bytes = create_webtoon_image(script, cat)
    print(f"  {W}×{H}px  {len(image_bytes)//1024}KB")

    # caption 구성 (CTA 랜덤 + 고지 포함)
    base_caption = script.get("caption", "")
    cta = random.choice(CTA_PHRASES)
    disclosure = "쿠팡파트너스 활동으로 수수료를 받을 수 있어요."
    if "쿠팡파트너스" not in base_caption:
        caption = f"{base_caption}\n{cta}\n{disclosure}"
    else:
        caption = base_caption

    print(f"\n📝 캡션 미리보기:")
    print(f"{'─'*40}")
    print(caption)
    print(f"{'─'*40}")

    # ── DRY-RUN: 로컬 저장 후 종료 ───────────────────────────
    if DRY_RUN:
        save_path = SCRIPT_DIR / f"dry_run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        with open(save_path, "wb") as f:
            f.write(image_bytes)
        print(f"\n✅ [DRY-RUN] 이미지 저장 완료: {save_path.name}")
        print("   GitHub 업로드 및 Threads 게시는 생략됐습니다.")
        return

    # ── 실제 게시 ─────────────────────────────────────────────
    filename = f"webtoon_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
    print(f"\n📤 GitHub 업로드: images/{filename}")
    raw_url = upload_image(image_bytes, filename)

    print("  ⏳ CDN 대기 (20초)...")
    time.sleep(20)

    print("\n📱 Threads 게시 중...")
    post_id = post_image_to_threads(raw_url, caption)
    if not post_id:
        print("❌ 게시 실패")
        return

    time.sleep(3)
    print("\n💬 링크 댓글 추가 중...")
    post_link_comment(post_id, cat["link"])

    print(f"\n🎉 완료! [{cat['name']}]")


if __name__ == "__main__":
    main()
