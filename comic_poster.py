"""
꿀템연구소 미스터리형 6컷 웹툰 자동화 v4
명세: COWORK_MYSTERY_WEBTOON_SPEC.md
"""
import os, re, sys, json, time, base64, random, textwrap, shutil, statistics
from pathlib import Path
from datetime import datetime
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
import anthropic
import openai
import requests
import naver_shopping

# ── CLI 플래그 ──────────────────────────────────────────────────────
_ARGS         = set(sys.argv[1:])
DRY_RUN       = "--dry-run"            in _ARGS
SCRIPT_ONLY   = "--script-only"        in _ARGS
GEN_REFERENCE = "--generate-reference" in _ARGS
SKIP_IMG_API  = "--skip-image-api"     in _ARGS
QA_ONLY       = "--quality-check-only" in _ARGS
PRODUCT_INDEX = next(
    (int(a.split("=")[1]) for a in _ARGS if a.startswith("--product-index=")), None
)

# ── 경로 ───────────────────────────────────────────────────────────
SCRIPT_DIR     = Path(__file__).parent
ASSETS_DIR     = SCRIPT_DIR / "assets"
COMICS_DIR     = SCRIPT_DIR / "generated_comics"
CHARACTER_PATH = SCRIPT_DIR / "character.png"
REFERENCE_PATH = ASSETS_DIR / "character_reference.png"
ASSETS_DIR.mkdir(exist_ok=True)
COMICS_DIR.mkdir(exist_ok=True)

# ── 환경변수 ───────────────────────────────────────────────────────
_ENV = {k: os.environ.get(k, "") for k in [
    "CLAUDE_API_KEY","OPENAI_API_KEY",
    "THREADS_ACCESS_TOKEN","THREADS_USER_ID","GITHUB_TOKEN",
]}
IMAGE_MODEL     = os.environ.get("OPENAI_IMAGE_MODEL", "gpt-image-1")
IMAGE_QUALITY   = os.environ.get("IMAGE_QUALITY", "medium")
MAX_RETRIES     = int(os.environ.get("MAX_PANEL_RETRIES", "2"))
DAILY_IMG_LIMIT = int(os.environ.get("DAILY_IMAGE_LIMIT", "24"))
AUTO_PUBLISH    = os.environ.get("AUTO_PUBLISH","false").lower() == "true"
GITHUB_REPO     = "a01030589992-dotcom/threads-auto-poster"
GITHUB_BRANCH   = "main"

def _validate_env():
    need = ["CLAUDE_API_KEY","OPENAI_API_KEY"]
    if not (DRY_RUN or SCRIPT_ONLY or GEN_REFERENCE or SKIP_IMG_API or QA_ONLY):
        need += ["THREADS_ACCESS_TOKEN","THREADS_USER_ID","GITHUB_TOKEN"]
    missing = [k for k in need if not _ENV[k]]
    if missing:
        print(f"필수 환경변수 누락: {', '.join(missing)}")
        sys.exit(1)

# ── 캔버스 상수 ────────────────────────────────────────────────────
W, H   = 1080, 1920
MX     = 16
MY     = 18
GUTTER = 10
LOGO_H = 64

_avail  = H - MY*2 - LOGO_H - GUTTER - GUTTER*5
PANEL_H = _avail // 6
PANEL_W = W - MX*2

def _prect(i):
    y = MY + i*(PANEL_H + GUTTER)
    return (MX, y, PANEL_W, PANEL_H)

LOGO_Y   = MY + 6*(PANEL_H + GUTTER) + 4
PANEL_BG = ["#FFFDE7","#FFF3E0","#FCE4EC","#E8F5E9","#E3F2FD","#FFF9C4"]
BG_COLOR = "#F5F5F0"
BORDER   = "#333333"
BUBBLE   = "#FFFFFF"
TEXT_C   = "#1A1A1A"

BODY_CTA = [
    "뭘 사용한 건지는 댓글에서 확인.",
    "물음표 아이템 정체는 댓글에 있어요.",
    "뭔지 궁금하면 댓글 확인해보세요.",
    "정체는 댓글에 적어뒀어요.",
]
DISCLOSURE = (
    "※ 이 포스팅은 쿠팡 파트너스 활동의 일환으로,\n"
    "이에 따른 일정액의 수수료를 제공받습니다."
)

# ── 카테고리 ───────────────────────────────────────────────────────
CATEGORIES = [
    {"name":"자취생 필수템","link":"https://link.coupang.com/a/e0J5NRuVIy",
     "naver_query":"자취 필수템 생활용품","location":"자취방","problem_area":"정리/수납"},
    {"name":"여름 시즌 아이템","link":"https://link.coupang.com/a/e0J8XB3t7s",
     "naver_query":"여름 더위 냉감 용품","location":"침실/거실","problem_area":"더위/냉각"},
    {"name":"주방가전","link":"https://link.coupang.com/a/e0KcjeIb7I",
     "naver_query":"자취 소형 주방가전","location":"주방","problem_area":"요리/식사"},
    {"name":"영양제/건강식품","link":"https://link.coupang.com/a/e0Ke9Db6uy",
     "naver_query":"20대 직장인 영양제","location":"책상/침실","problem_area":"건강/피로"},
]

# ── 폰트 ───────────────────────────────────────────────────────────
def _load_fonts():
    bold_cands = [
        "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
        "C:/Windows/Fonts/malgunbd.ttf","C:/Windows/Fonts/gulim.ttc",
    ]
    reg_cands = [
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        "C:/Windows/Fonts/malgun.ttf","C:/Windows/Fonts/gulim.ttc",
    ]
    bold = next((p for p in bold_cands if Path(p).exists()), None)
    reg  = next((p for p in reg_cands  if Path(p).exists()), None)
    def fnt(path, size):
        if path:
            try: return ImageFont.truetype(path, size)
            except Exception: pass
        return ImageFont.load_default()
    return {
        "bubble": fnt(bold, 32), "narr": fnt(bold, 36),
        "logo":   fnt(bold, 22), "small": fnt(reg, 20),
        "qmark":  fnt(bold, 110),
    }

# ── 캐릭터 기준표 ─────────────────────────────────────────────────
def generate_character_reference():
    if REFERENCE_PATH.exists():
        print(f"  기준표 이미 존재: {REFERENCE_PATH.name}")
        return REFERENCE_PATH
    print("  캐릭터 기준표 생성 중...")
    client = openai.OpenAI(api_key=_ENV["OPENAI_API_KEY"])
    prompt = (
        "Korean webtoon character reference sheet: friendly Korean male 20s, "
        "black hair, casual clothes. Show front/left/right full body and 4 expressions "
        "(thinking, surprised, happy, neutral). Same hairstyle, face, outfit throughout. "
        "Bright clean Korean life-comedy webtoon style. White background. "
        "No text, no logo, no watermark. 3x3 grid."
    )
    try:
        resp = client.images.generate(
            model=IMAGE_MODEL, prompt=prompt,
            size="1024x1024", quality=IMAGE_QUALITY, n=1,
        )
        data = _img_bytes(resp.data[0])
        img  = Image.open(BytesIO(data)).convert("RGB")
        img.save(REFERENCE_PATH)
        print(f"  기준표 저장: {REFERENCE_PATH.name}")
    except Exception as e:
        print(f"  기준표 생성 실패: {e}")
        if CHARACTER_PATH.exists():
            shutil.copy(CHARACTER_PATH, REFERENCE_PATH)
    return REFERENCE_PATH

def _img_bytes(obj):
    if hasattr(obj,"b64_json") and obj.b64_json:
        return base64.b64decode(obj.b64_json)
    if hasattr(obj,"url") and obj.url:
        r = requests.get(obj.url, timeout=30)
        r.raise_for_status()
        return r.content
    raise ValueError("이미지 데이터 없음")

# ── 대본 생성 (Claude) ────────────────────────────────────────────
def generate_script(cat, products, anon_data):
    client = anthropic.Anthropic(api_key=_ENV["CLAUDE_API_KEY"])
    prompt = f"""너는 꿀템연구소 6컷 웹툰 작가야.
장소: {anon_data['location']} / 불편: {anon_data['problem_area']} / 카테고리: {cat['name']}

[6컷 흐름]
1 trouble: 공감되는 생활 불편
2 try_fail: 기존 방법 시도 → 안 됨
3 insight: 진짜 원인 발견
4 mystery: "?" 정체불명 아이템 등장 (상품명 절대 금지)
5 result: 달라진 결과만 (상품 안 보임)
6 cta: 댓글 유도 (상품명 금지)

[규칙]
- 대사 1개, 8~18자
- 상품명/브랜드 금지
- scene_prompt: 영어, 캐릭터=Korean male 20s black hair casual clothes
- 4컷 scene: 반드시 "box with large ? mark" 포함
- 5컷 scene: "tidy space, no product visible, character satisfied"
- caption: 120~180자, 상품명 없음, 고지 포함
- comment_body: 댓글용, 상품명 자리는 [PRODUCT]

JSON만 출력:
{{"panels":[
  {{"type":"trouble","text":"대사","scene_prompt":"english"}},
  {{"type":"try_fail","text":"대사","scene_prompt":"english"}},
  {{"type":"insight","text":"대사","scene_prompt":"english"}},
  {{"type":"mystery","text":"대사","scene_prompt":"english ? box"}},
  {{"type":"result","text":"대사","scene_prompt":"english result only"}},
  {{"type":"cta","text":"대사","scene_prompt":"english"}}
],
"caption":"본문 120~180자",
"comment_body":"댓글 텍스트 [PRODUCT] 포함"}}"""
    msg = client.messages.create(
        model="claude-sonnet-4-6", max_tokens=1000,
        messages=[{"role":"user","content":prompt}],
    )
    raw = msg.content[0].text.strip()
    m   = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        raise ValueError(f"JSON 파싱 실패:\n{raw[:300]}")
    return json.loads(m.group())

# ── 컷 이미지 생성 (OpenAI) ───────────────────────────────────────
_CHAR = (
    "Friendly Korean male character in his 20s, black hair, casual clothes. "
    "Bright clean Korean life-comedy webtoon art style. "
    "No text, no logo, no watermark, no brand names, no product labels. "
    "Simple domestic interior background. Wide cinematic panel composition."
)
_FORBID = (
    "No text, no speech bubbles, no captions, no product logos, "
    "no watermarks, no dark explanation boxes."
)
_IMG_COUNT = 0

def generate_panel_image(panel, idx, output_dir, ref_b64=""):
    global _IMG_COUNT
    out = output_dir / f"panel_{idx+1:02d}.png"
    if SKIP_IMG_API and out.exists():
        print(f"    [skip] 기존 컷 재사용: {out.name}")
        return out
    if _IMG_COUNT >= DAILY_IMG_LIMIT:
        raise RuntimeError(f"일일 이미지 한도 초과({DAILY_IMG_LIMIT})")

    client = openai.OpenAI(api_key=_ENV["OPENAI_API_KEY"])
    ptype  = panel.get("type","")
    scene  = panel.get("scene_prompt","")

    if ptype == "mystery":
        scene = ("Korean male character curiously examining a cardboard box "
                 "with a large bright red question mark on it. " + scene)
    elif ptype == "result":
        scene = ("Tidy organized domestic space. No product visible. "
                 "Character looks relaxed and satisfied. " + scene)

    full_prompt = f"{_CHAR} Scene: {scene} {_FORBID}"

    for attempt in range(MAX_RETRIES + 1):
        try:
            _IMG_COUNT += 1
            print(f"    컷 {idx+1} 생성 중 (시도 {attempt+1}/{MAX_RETRIES+1})...")
            resp = client.images.generate(
                model=IMAGE_MODEL, prompt=full_prompt,
                size="1024x1024", quality=IMAGE_QUALITY, n=1,
            )
            data = _img_bytes(resp.data[0])
            Image.open(BytesIO(data)).convert("RGB").save(out)
            print(f"    컷 {idx+1} 저장: {out.name}")
            return out
        except Exception as e:
            print(f"    컷 {idx+1} 실패 (시도 {attempt+1}): {e}")
            if attempt >= MAX_RETRIES:
                raise RuntimeError(f"컷 {idx+1} 최종 실패: {e}")
            time.sleep(3*(attempt+1))
    raise RuntimeError(f"컷 {idx+1} 생성 실패")

# ── 말풍선 ────────────────────────────────────────────────────────
def _draw_bubble(draw, text, bx, by, bw, bh, font):
    pad    = 10
    lines  = textwrap.wrap(text, width=max(6, bw//(font.size//2+2)))[:2]
    bub_h  = min(len(lines)*(font.size+6)+pad*2+6, bh-24)
    y1, y2 = by+6, by+6+bub_h
    draw.rounded_rectangle([bx+pad, y1, bx+bw-pad, y2],
                            radius=14, fill=BUBBLE, outline=BORDER, width=2)
    cx = bx+bw//2
    draw.polygon([(cx-10,y2),(cx+10,y2),(cx,y2+14)], fill=BUBBLE)
    draw.line([(cx-10,y2),(cx,y2+14)], fill=BORDER, width=2)
    draw.line([(cx+10,y2),(cx,y2+14)], fill=BORDER, width=2)
    ty = y1+pad
    for line in lines:
        draw.text((cx, ty+font.size//2), line, font=font, fill=TEXT_C, anchor="mm")
        ty += font.size+6

def _qmark_overlay(img, fonts):
    ov = Image.new("RGBA", img.size, (0,0,0,0))
    d  = ImageDraw.Draw(ov)
    cx, cy = img.width//2, img.height//2
    r = min(img.width, img.height)//3
    d.ellipse([cx-r,cy-r,cx+r,cy+r], fill=(220,50,50,200))
    d.text((cx,cy), "?", font=fonts["qmark"], fill=(255,255,255,230), anchor="mm")
    return Image.alpha_composite(img.convert("RGBA"), ov).convert("RGB")

# ── Pillow 합성 ───────────────────────────────────────────────────
def compose_webtoon(script, panel_paths, output_path, fonts):
    canvas = Image.new("RGB", (W,H), BG_COLOR)
    draw   = ImageDraw.Draw(canvas)
    panels = script.get("panels",[])
    for idx, ip in enumerate(panel_paths[:6]):
        px, py, pw, ph = _prect(idx)
        meta   = panels[idx] if idx<len(panels) else {}
        ptype  = meta.get("type","")
        text   = meta.get("text","")
        try:
            raw = Image.open(ip).convert("RGB")
            scale = pw/raw.width; nh = int(raw.height*scale)
            r = raw.resize((pw,nh), Image.LANCZOS)
            if nh > ph:
                top = (nh-ph)//2; r = r.crop((0,top,pw,top+ph))
            else:
                bg = Image.new("RGB",(pw,ph),PANEL_BG[idx])
                bg.paste(r,(0,(ph-nh)//2)); r = bg
            if ptype=="mystery": r = _qmark_overlay(r, fonts)
            canvas.paste(r,(px,py))
        except Exception as e:
            print(f"  컷{idx+1} 로드 실패: {e}")
            draw.rectangle([px,py,px+pw,py+ph], fill=PANEL_BG[idx])
        draw.rectangle([px,py,px+pw,py+ph], outline=BORDER, width=2)
        nr=12
        draw.ellipse([px+6,py+6,px+6+nr*2,py+6+nr*2], fill=BORDER)
        draw.text((px+6+nr,py+6+nr), str(idx+1),
                  font=fonts["small"], fill="#FFF", anchor="mm")
        if text:
            _draw_bubble(draw, text, px, py, pw, ph, fonts["bubble"])
    lx1,ly1 = MX, LOGO_Y
    lx2,ly2 = W-MX, LOGO_Y+LOGO_H
    draw.rectangle([lx1,ly1,lx2,ly2], fill="#FFF8E1", outline=BORDER, width=2)
    draw.text((W//2,(ly1+ly2)//2),
              "꿀템연구소  |  물음표의 정체는 댓글에서 확인",
              font=fonts["logo"], fill="#5D4037", anchor="mm")
    canvas.save(output_path, format="PNG", optimize=True)
    kb = output_path.stat().st_size//1024
    print(f"  최종 웹툰 저장: {output_path.name} ({W}x{H}px, {kb}KB)")
    return output_path

# ── 품질검사 ─────────────────────────────────────────────────────
def run_quality_check(final_path, script, panel_paths, product, output_dir):
    rep = {"timestamp":datetime.now().isoformat(),"status":"passed",
           "checks":{},"warnings":[],"errors":[]}
    def fail(k,m): rep["checks"][k]="FAIL"; rep["errors"].append(m); rep.__setitem__("status","failed")
    def warn(k,m): rep["checks"][k]="WARN"; rep["warnings"].append(m)
    def ok(k):     rep["checks"][k]="OK"

    if len(panel_paths)==6: ok("panel_count")
    else: fail("panel_count",f"패널 수 오류: {len(panel_paths)}")

    if final_path.exists():
        img = Image.open(final_path)
        if img.size==(W,H): ok("final_size")
        else: fail("final_size",f"크기 오류: {img.size}")
        avg = statistics.mean(img.convert("L").getdata())
        if avg>30: ok("not_black")
        else: fail("not_black","이미지 너무 어두움")
    else:
        fail("final_exists","최종 이미지 없음")

    for i,p in enumerate(script.get("panels",[])):
        t=p.get("text","")
        if len(t)>20: warn(f"p{i+1}_len",f"컷{i+1} 대사 {len(t)}자")
        else: ok(f"p{i+1}_len")

    cap = script.get("caption","")
    if "수수료" in cap or "파트너스" in cap: ok("disclosure")
    else: fail("disclosure","광고 고지 누락")

    if product.get("link") or product.get("affiliate_url"): ok("affiliate_link")
    else: fail("affiliate_link","제휴 링크 없음")

    pname = product.get("product_name","")
    if pname and pname.lower() in cap.lower():
        fail("product_secret",f"캡션에 상품명 노출: {pname}")
    else: ok("product_secret")

    if final_path.exists() and _ENV["CLAUDE_API_KEY"]:
        try: _vision_qa(final_path, rep)
        except Exception as e: warn("vision_qa",f"비전QA 오류: {e}")

    rp = output_dir/"quality_report.json"
    rp.write_text(json.dumps(rep, ensure_ascii=False, indent=2))
    print(f"\n  품질검사: {rep['status'].upper()}")
    for e in rep["errors"]:   print(f"    FAIL: {e}")
    for w in rep["warnings"]: print(f"    WARN: {w}")
    return rep

def _vision_qa(img_path, rep):
    client = anthropic.Anthropic(api_key=_ENV["CLAUDE_API_KEY"])
    b64    = base64.b64encode(img_path.read_bytes()).decode()
    qa_q   = (
        'JSON only: {"webtoon_style":{"pass":true,"reason":""},'
        '"looks_ad":{"pass":false,"reason":""},'
        '"product_exposed":{"pass":false,"reason":""},'
        '"has_text_in_img":{"pass":false,"reason":""},'
        '"anatomy_ok":{"pass":true,"reason":""}}\n'
        "Evaluate this 6-panel webtoon image."
    )
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001", max_tokens=400,
        messages=[{"role":"user","content":[
            {"type":"image","source":{"type":"base64","media_type":"image/png","data":b64}},
            {"type":"text","text":qa_q},
        ]}],
    )
    m = re.search(r"\{.*\}", resp.content[0].text, re.DOTALL)
    if not m: return
    qa = json.loads(m.group())
    for key,val in qa.items():
        passed = val.get("pass",True)
        reason = val.get("reason","")
        if passed:
            rep["checks"][f"v_{key}"] = "OK"
        elif key in ("product_exposed","looks_ad","has_text_in_img"):
            rep["checks"][f"v_{key}"] = "FAIL"
            rep["errors"].append(f"비전QA {key}: {reason}")
            rep["status"] = "failed"
        else:
            rep["checks"][f"v_{key}"] = "WARN"
            rep["warnings"].append(f"비전QA {key}: {reason}")

# ── GitHub 업로드 ─────────────────────────────────────────────────
def upload_to_github(img_bytes, filename):
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/images/{filename}"
    hdr = {"Authorization":f"token {_ENV['GITHUB_TOKEN']}",
           "Accept":"application/vnd.github.v3+json"}
    sha = None
    try:
        r = requests.get(url, headers=hdr, timeout=10)
        if r.status_code==200: sha = r.json().get("sha")
    except Exception: pass
    payload = {"message":f"webtoon: {filename}",
               "content":base64.b64encode(img_bytes).decode(),
               "branch":GITHUB_BRANCH}
    if sha: payload["sha"] = sha
    r = requests.put(url, headers=hdr, json=payload, timeout=30)
    if r.status_code not in (200,201):
        raise RuntimeError(f"GitHub 업로드 실패: {r.status_code}")
    raw = f"https://raw.githubusercontent.com/{GITHUB_REPO}/{GITHUB_BRANCH}/images/{filename}"
    print(f"  GitHub 업로드 완료: {filename}")
    return raw

# ── Threads 게시 ─────────────────────────────────────────────────
def post_image_to_threads(image_url, caption):
    res = requests.post(
        f"https://graph.threads.net/v1.0/{_ENV['THREADS_USER_ID']}/threads",
        data={"media_type":"IMAGE","image_url":image_url,
              "text":caption,"access_token":_ENV["THREADS_ACCESS_TOKEN"]},
        timeout=20,
    )
    cid = res.json().get("id")
    if not cid:
        print(f"  컨테이너 생성 실패: {res.json()}")
        return None
    time.sleep(5)
    pub = requests.post(
        f"https://graph.threads.net/v1.0/{_ENV['THREADS_USER_ID']}/threads_publish",
        data={"creation_id":cid,"access_token":_ENV["THREADS_ACCESS_TOKEN"]},
        timeout=20,
    )
    pid = pub.json().get("id")
    if pid: print(f"  이미지 게시 완료: {pid}")
    return pid

def post_comment_with_retry(post_id, comment_text, max_retry=3):
    for attempt in range(1, max_retry+1):
        try:
            res = requests.post(
                f"https://graph.threads.net/v1.0/{_ENV['THREADS_USER_ID']}/threads",
                data={"media_type":"TEXT","text":comment_text,
                      "reply_to_id":post_id,"access_token":_ENV["THREADS_ACCESS_TOKEN"]},
                timeout=15,
            )
            cid = res.json().get("id")
            if not cid: raise ValueError(f"컨테이너 없음: {res.json()}")
            time.sleep(2)
            pub = requests.post(
                f"https://graph.threads.net/v1.0/{_ENV['THREADS_USER_ID']}/threads_publish",
                data={"creation_id":cid,"access_token":_ENV["THREADS_ACCESS_TOKEN"]},
                timeout=15,
            )
            if "id" in pub.json():
                print("  댓글 게시 완료")
                return True
            raise ValueError(f"게시 실패: {pub.json()}")
        except Exception as e:
            print(f"  댓글 시도 {attempt}: {e}")
            if attempt < max_retry: time.sleep(5*attempt)
    print(f"  댓글 최종 실패 (post_id={post_id})")
    return False

# ── 메인 ─────────────────────────────────────────────────────────
def main():
    _validate_env()

    if GEN_REFERENCE:
        print("\n캐릭터 기준표 생성 모드")
        generate_character_reference()
        return

    mode = "DRY-RUN" if DRY_RUN else "실제 게시"
    print(f"\n{'='*56}")
    print(f"꿀템연구소 미스터리 웹툰 [{mode}]")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"   모델: {IMAGE_MODEL} / 품질: {IMAGE_QUALITY}")
    print(f"{'='*56}")

    now = datetime.now()
    cat = (CATEGORIES[PRODUCT_INDEX % len(CATEGORIES)] if PRODUCT_INDEX is not None
           else CATEGORIES[(now.day + now.hour//8) % len(CATEGORIES)])
    product_id  = re.sub(r"\W+","-",cat["name"].lower())
    output_dir  = COMICS_DIR / f"{now.strftime('%Y-%m-%d')}_{product_id}"
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"\n카테고리: {cat['name']}")

    print(f"\n네이버 쇼핑 수집 중... [{cat['naver_query']}]")
    products    = naver_shopping.fetch_products(cat["naver_query"], count=5)
    pname       = products[0]["name"] if products else "생활 아이템"

    private_product = {
        "product_name": pname, "brand": "",
        "affiliate_url": cat["link"], "link": cat["link"],
        "original_image_url": products[0].get("image","") if products else "",
    }
    anon_data = {
        "problem": f"{cat['location']}에서 생기는 {cat['problem_area']} 불편",
        "location": cat["location"], "problem_area": cat["problem_area"],
        "result": "사용 후 달라진 상태", "mystery_label": "?",
        "generic_category": "생활 아이템",
    }
    (output_dir/"private_product.json").write_text(
        json.dumps(private_product, ensure_ascii=False, indent=2))
    (output_dir/"public_story_input.json").write_text(
        json.dumps(anon_data, ensure_ascii=False, indent=2))

    print("\n익명화 대본 생성 중...")
    script = generate_script(cat, products, anon_data)
    (output_dir/"script.json").write_text(
        json.dumps(script, ensure_ascii=False, indent=2))
    panels = script.get("panels",[])
    print(f"  컷 수: {len(panels)}")
    for i,p in enumerate(panels,1):
        print(f"    [{i}] {p.get('type',''):10s}| {p.get('text','')}")

    if SCRIPT_ONLY:
        print("\n[script-only] 대본 완료.")
        return

    # 캐릭터 기준표
    ref_b64 = ""
    if not REFERENCE_PATH.exists() and CHARACTER_PATH.exists():
        try: generate_character_reference()
        except Exception as e: print(f"  기준표 생성 실패: {e}")
    if REFERENCE_PATH.exists():
        ref_b64 = base64.b64encode(REFERENCE_PATH.read_bytes()).decode()
        print(f"\n  캐릭터 기준표 로드: {REFERENCE_PATH.name}")

    # 컷 생성
    print("\n6컷 이미지 생성 중...")
    panel_paths, failed = [], []
    for idx,panel in enumerate(panels[:6]):
        try:
            panel_paths.append(generate_panel_image(panel, idx, output_dir, ref_b64))
        except Exception as e:
            print(f"  컷{idx+1} 실패: {e}")
            failed.append(idx)
            blank = Image.new("RGB",(1024,1024),PANEL_BG[idx])
            bp = output_dir/f"panel_{idx+1:02d}.png"
            blank.save(bp); panel_paths.append(bp)

    # 합성
    print("\n최종 웹툰 합성 중...")
    fonts      = _load_fonts()
    final_path = output_dir/"final_webtoon.png"
    compose_webtoon(script, panel_paths, final_path, fonts)

    # 캡션
    base_cap = script.get("caption","")
    cta      = random.choice(BODY_CTA)
    caption  = (f"{base_cap}\n\n{cta}\n{DISCLOSURE}"
                if "수수료" not in base_cap else base_cap)
    raw_cmnt = script.get("comment_body","[PRODUCT]")
    comment  = (f"웹툰 속 물음표 아이템\n"
                f"{raw_cmnt.replace('[PRODUCT]',pname)}\n\n"
                f"{cat['link']}\n\n"
                "※ 쿠팡 파트너스 활동을 통해 일정액의 수수료를 제공받을 수 있습니다.")
    (output_dir/"post_body.txt").write_text(caption, encoding="utf-8")
    (output_dir/"comment.txt").write_text(comment, encoding="utf-8")
    print(f"\n캡션 미리보기:\n{'─'*44}\n{caption}\n{'─'*44}")

    # 품질검사
    print("\n품질검사 실행 중...")
    qa = run_quality_check(final_path, script, panel_paths, private_product, output_dir)
    if failed:
        qa["status"]="failed"
        qa["errors"].append(f"생성 실패 컷: {[i+1 for i in failed]}")

    if QA_ONLY:
        print("\n[quality-check-only] 완료.")
        return

    if qa["status"]=="failed":
        print("\n품질검사 실패 — 게시 중단. quality_report.json 확인.")
        return

    if DRY_RUN:
        print(f"\n[DRY-RUN] 완료.\n  이미지: {final_path}\n  폴더: {output_dir}")
        return

    if not AUTO_PUBLISH:
        print("\nAUTO_PUBLISH=false — 게시 생략.")
        print("환경변수 AUTO_PUBLISH=true 설정 후 게시 활성화됩니다.")
        return

    # 실제 게시
    print("\nGitHub 업로드 중...")
    ts      = now.strftime("%Y%m%d_%H%M%S")
    raw_url = upload_to_github(final_path.read_bytes(), f"webtoon_{ts}.png")
    print("  CDN 대기 (20초)...")
    time.sleep(20)
    print("\nThreads 게시 중...")
    pid = post_image_to_threads(raw_url, caption)
    if not pid:
        print("게시 실패")
        return
    time.sleep(3)
    print("\n댓글 추가 중...")
    ok_c = post_comment_with_retry(pid, comment)
    if not ok_c:
        fl = {"post_id":pid,"comment":comment,"failed_at":now.isoformat()}
        (output_dir/"comment_fail.json").write_text(json.dumps(fl,ensure_ascii=False,indent=2))
    print(f"\n완료! [{cat['name']}]\n  결과물: {output_dir}")

if __name__ == "__main__":
    main()
