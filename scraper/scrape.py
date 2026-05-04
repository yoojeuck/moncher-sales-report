"""
몬쉘 판매일보 스크래퍼 v2
- 특정 날짜 또는 전체 기간 게시글 수집
- Gemini AI 요약 + 업무 과제 생성
- Google Sheets 업데이트 (원문 + 요약 시트)
"""

import os, json, re, sys, time
from datetime import datetime, timezone, timedelta
import requests

# ── 설정 ──────────────────────────────────────────────────────────
BASE_URL       = "https://monchouchou.daouoffice.com"
COMPANY_ID     = "5000001396"
BOARD_ID       = "17422"
LOGIN_ID       = os.environ.get("DAOU_ID", "juyoo")
PASSWORD       = os.environ.get("DAOU_PW", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
KST            = timezone(timedelta(hours=9))

_base        = os.path.join(os.path.dirname(__file__), "..")
DATA_FILE    = os.path.join(_base, "data", "posts.json")
SUMMARY_FILE = os.path.join(_base, "data", "summaries.json")


# ── 1. 로그인 ────────────────────────────────────────────────────
def login() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Content-Type": "application/json",
        "X-Referer-Info": "monchouchou.daouoffice.com",
        "Referer": f"{BASE_URL}/login",
        "Origin": BASE_URL,
    })
    resp = session.post(f"{BASE_URL}/api/portal/public/auth/login", json={
        "companyId": COMPANY_ID, "loginId": LOGIN_ID,
        "password": PASSWORD, "captcha": "", "locale": "ko",
    })
    resp.raise_for_status()
    token = session.cookies.get("AccessToken")
    if not token:
        raise RuntimeError("로그인 실패: AccessToken을 받지 못했습니다.")
    session.headers["Authorization"] = f"Bearer {token}"
    print("[✓] 로그인 성공")
    return session


# ── 2. 게시글 내용 처리 ──────────────────────────────────────────
def strip_html(text: str) -> str:
    """HTML 태그 제거 및 줄바꿈 정리"""
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<p\s*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</p>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'&nbsp;', ' ', text)
    text = re.sub(r'&amp;', '&', text)
    text = re.sub(r'&lt;', '<', text)
    text = re.sub(r'&gt;', '>', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def fetch_post_full_content(session: requests.Session, post_id: str) -> str | None:
    """개별 게시글 전체 내용 시도 (여러 엔드포인트)"""
    endpoints = [
        f"{BASE_URL}/gw/api/board/{BOARD_ID}/posts/{post_id}",
        f"{BASE_URL}/gw/api/v1/board/{BOARD_ID}/post/{post_id}",
        f"{BASE_URL}/gw/api/community/board/{BOARD_ID}/article/{post_id}",
    ]
    for url in endpoints:
        try:
            resp = session.get(url, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                d = data.get("data") or data
                content = d.get("content") or d.get("body") or d.get("text") or d.get("html")
                if content and len(str(content)) > 50:
                    return strip_html(str(content))
        except Exception:
            pass
    return None


def parse_post(post: dict, session: requests.Session, try_full: bool = True) -> dict:
    """API 응답 -> 정규화된 dict"""
    created = post.get("createdAt", "")
    post_date = created[:10] if created else ""
    post_id = str(post.get("id", ""))

    content = strip_html(post.get("summary", "").strip())

    # 전체 내용 시도 (더 길면 교체)
    if try_full and post_id:
        full = fetch_post_full_content(session, post_id)
        if full and len(full) > len(content):
            content = full

    return {
        "id":        post_id,
        "date":      post_date,
        "createdAt": created,
        "store":     post.get("writer", {}).get("name", ""),
        "content":   content,
        "writer_id": post.get("writerId"),
    }


# ── 3. 특정 날짜 수집 ────────────────────────────────────────────
def fetch_posts_for_date(session: requests.Session, target_date: str) -> list[dict]:
    print(f"[>] {target_date} 게시글 수집 중...")
    result = []
    page = 0
    while True:
        resp = session.get(f"{BASE_URL}/gw/api/board/{BOARD_ID}/posts",
                           params={"page": page, "size": 30})
        resp.raise_for_status()
        data = resp.json()
        posts = data.get("data", [])
        if not posts:
            break
        for post in posts:
            created = post.get("createdAt", "")
            post_date = created[:10] if created else ""
            if post_date == target_date:
                result.append(parse_post(post, session, try_full=True))
            elif post_date < target_date:
                return result
        if not data.get("hasNext", False):
            break
        page += 1
    return result


# ── 4. 전체 기간 수집 (최초 1회) ────────────────────────────────
def fetch_all_posts(session: requests.Session) -> dict:
    print("[>] 전체 기간 게시글 수집 시작 (시간이 걸릴 수 있습니다)...")
    all_data: dict[str, list] = {}
    page = 0
    total = 0
    while True:
        resp = session.get(f"{BASE_URL}/gw/api/board/{BOARD_ID}/posts",
                           params={"page": page, "size": 30})
        resp.raise_for_status()
        data = resp.json()
        posts_raw = data.get("data", [])
        if not posts_raw:
            break
        for post in posts_raw:
            # 전체 수집 시엔 개별 API 호출 생략 (속도 우선)
            parsed = parse_post(post, session, try_full=False)
            d = parsed["date"]
            if d:
                all_data.setdefault(d, []).append(parsed)
                total += 1
        print(f"  페이지 {page+1}: {len(posts_raw)}건 (누계 {total}건)")
        if not data.get("hasNext", False):
            break
        page += 1
        time.sleep(0.3)
    print(f"[✓] 전체 수집 완료: {total}건, {len(all_data)}일")
    return all_data


# ── 5. Gemini AI 요약 ────────────────────────────────────────────
def generate_summary(posts: list[dict], target_date: str) -> dict:
    if not GEMINI_API_KEY:
        print("[!] GEMINI_API_KEY 미설정 — 요약 건너뜁니다.")
        return {}
    if not posts:
        return {}

    posts_text = "\n\n".join(
        f"[{p['store']}]\n{p['content']}"
        for p in posts if p.get("content")
    )

    prompt = f"""{target_date} 몬쉘 각 매장의 판매일보입니다.

{posts_text}

아래 JSON 형식으로만 응답해주세요 (설명 없이 JSON만):
{{
  "overall_summary": "전체 매장 상황을 2-3문장으로 요약",
  "store_summaries": {{
    "매장명": "해당 매장 핵심 내용 1-2문장"
  }},
  "tasks": [
    {{
      "priority": "즉시처리 또는 이번주 또는 모니터링",
      "store": "매장명 또는 전체",
      "action": "구체적 업무 내용"
    }}
  ],
  "highlights": ["주목할 사항1", "주목할 사항2"]
}}"""

    try:
        resp = requests.post(
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent",
            headers={"Content-Type": "application/json", "X-goog-api-key": GEMINI_API_KEY},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.3, "responseMimeType": "application/json"}
            },
            timeout=30
        )
        resp.raise_for_status()
        text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        result = json.loads(text)
        print("[✓] AI 요약 생성 완료")
        return result
    except Exception as e:
        print(f"[!] AI 요약 실패: {e}")
        return {}


# ── 6. 저장 ─────────────────────────────────────────────────────
def save_posts(posts_by_date: dict):
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    existing = {}
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            existing = json.load(f)
    existing.update(posts_by_date)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)
    print(f"[✓] posts.json 저장 완료")


def save_summary(target_date: str, summary: dict):
    if not summary:
        return
    os.makedirs(os.path.dirname(SUMMARY_FILE), exist_ok=True)
    existing = {}
    if os.path.exists(SUMMARY_FILE):
        with open(SUMMARY_FILE, "r", encoding="utf-8") as f:
            existing = json.load(f)
    existing[target_date] = summary
    with open(SUMMARY_FILE, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)
    print(f"[✓] summaries.json 저장 완료")


# ── 7. Google Sheets ─────────────────────────────────────────────
def update_google_sheets(posts: list[dict], summary: dict, target_date: str):
    creds_json     = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    spreadsheet_id = os.environ.get("SPREADSHEET_ID")
    if not creds_json or not spreadsheet_id:
        print("[!] Google Sheets 환경변수 미설정 — 건너뜁니다.")
        return
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        print("[!] gspread 미설치 — 건너뜁니다.")
        return

    creds = Credentials.from_service_account_info(
        json.loads(creds_json),
        scopes=["https://spreadsheets.google.com/feeds",
                "https://www.googleapis.com/auth/drive"]
    )
    gc = gspread.authorize(creds)
    ss = gc.open_by_key(spreadsheet_id)
    existing_titles = [ws.title for ws in ss.worksheets()]

    # 원문 시트 (가장 왼쪽)
    if target_date in existing_titles:
        ss.del_worksheet(ss.worksheet(target_date))
    ws = ss.add_worksheet(title=target_date, rows=300, cols=10, index=0)
    rows = [["작성일시", "매장명", "내용", "게시글ID"]]
    for p in posts:
        rows.append([p["createdAt"], p["store"], p["content"], p["id"]])
    ws.update("A1", rows)
    ws.format("A1:D1", {"textFormat": {"bold": True}})
    print(f"[✓] Google Sheets 원문 시트 생성: {target_date}")

    # 요약 시트 (두 번째)
    if summary:
        summary_title = f"{target_date}_요약"
        if summary_title in existing_titles:
            ss.del_worksheet(ss.worksheet(summary_title))
        ws2 = ss.add_worksheet(title=summary_title, rows=100, cols=3, index=1)
        s_rows = [["항목", "내용"]]
        s_rows.append(["📋 전체 요약", summary.get("overall_summary", "")])
        s_rows.append(["", ""])
        s_rows.append(["⚡ 업무 과제 (우선순위|매장|내용)", ""])
        for t in summary.get("tasks", []):
            s_rows.append(["", f"{t.get('priority','')} | {t.get('store','')} | {t.get('action','')}"])
        s_rows.append(["", ""])
        s_rows.append(["🔍 주목 사항", " / ".join(summary.get("highlights", []))])
        s_rows.append(["", ""])
        s_rows.append(["🏪 매장별 요약", ""])
        for store, text in summary.get("store_summaries", {}).items():
            s_rows.append([store, text])
        ws2.update("A1", s_rows)
        ws2.format("A1:B1", {"textFormat": {"bold": True}})
        print(f"[✓] Google Sheets 요약 시트 생성: {summary_title}")


# ── 메인 ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    args = sys.argv[1:]
    session = login()

    if "--all" in args:
        # 전체 기간 수집 모드 (최초 1회)
        all_data = fetch_all_posts(session)
        save_posts(all_data)
        print("[!] 전체 수집 완료. AI 요약은 일별 실행 시 생성됩니다.")

    else:
        # 특정 날짜 (또는 오늘) 수집
        target = next((a for a in args if not a.startswith("--")), None)
        if target is None:
            target = datetime.now(KST).strftime("%Y-%m-%d")

        posts = fetch_posts_for_date(session, target)
        if not posts:
            print(f"[!] {target} 에 등록된 게시글이 없습니다.")
            sys.exit(0)

        print(f"[✓] {len(posts)}건 수집 완료")
        save_posts({target: posts})

        summary = generate_summary(posts, target)
        save_summary(target, summary)

        update_google_sheets(posts, summary, target)

    print("[✓] 완료!")
