"""
몬쉘 판매일보 스크래퍼 v3
- 단일 날짜 / 기간(--from --to) / 전체(--all) 수집
- 기본: 어제 날짜 수집 (매일 오전 7시 KST 실행 → 전날 게시글)
- Gemini AI 요약 + 업무 과제 생성 (단일날짜/기본 실행 시만)
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


def relogin(session: requests.Session):
    """세션 만료 시 기존 session 객체를 재로그인으로 갱신"""
    print("[!] 세션 만료 (401) — 재로그인 시도...")
    new_session = login()
    session.headers.update(new_session.headers)
    session.cookies.update(new_session.cookies)
    print("[✓] 세션 갱신 완료")


# ── 2. 게시글 내용 처리 ──────────────────────────────────────────
def strip_html(text: str) -> str:
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
    created = post.get("createdAt", "")
    post_date = created[:10] if created else ""
    post_id = str(post.get("id", ""))

    content = strip_html(post.get("summary", "").strip())

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
    session_refreshed = False  # 세션 갱신은 1회만 허용
    while True:
        resp = session.get(f"{BASE_URL}/gw/api/board/{BOARD_ID}/posts",
                           params={"page": page, "size": 30})
        if resp.status_code == 401 and not session_refreshed:
            relogin(session)
            session_refreshed = True
            page = 0
            result = []
            continue
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


# ── 4. 기간 수집 ─────────────────────────────────────────────────
def fetch_posts_for_range(session: requests.Session, from_date: str, to_date: str) -> dict:
    """from_date ~ to_date 기간의 게시글 수집 (원문만 저장, AI 요약 제외)
    ※ Gemini 무료 티어 일일 토큰 한도 소진 방지를 위해 AI 요약은 건너뜁니다.
    """
    print(f"[>] 기간 수집: {from_date} ~ {to_date} (AI 요약 생략)")
    start = datetime.strptime(from_date, "%Y-%m-%d")
    end   = datetime.strptime(to_date,   "%Y-%m-%d")

    if start > end:
        print("[!] 시작일이 종료일보다 큽니다.")
        return {}

    all_data = {}
    current = start
    while current <= end:
        date_str = current.strftime("%Y-%m-%d")
        posts = fetch_posts_for_date(session, date_str)
        if posts:
            print(f"[✓] {date_str}: {len(posts)}건")
            save_posts({date_str: posts})
            update_google_sheets(posts, {}, date_str)  # summary={} → 요약 시트 생략
            all_data[date_str] = posts
        else:
            print(f"[−] {date_str}: 게시글 없음")
        current += timedelta(days=1)
        time.sleep(0.5)

    print(f"[✓] 기간 수집 완료: {len(all_data)}일치 데이터 (AI 요약은 별도 실행 필요)")
    return all_data


# ── 5. 전체 기간 수집 (최초 1회) ────────────────────────────────
def fetch_all_posts(session: requests.Session) -> dict:
    print("[>] 전체 기간 게시글 수집 시작...")
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


# ── 6. Gemini AI 요약 ────────────────────────────────────────────
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

    for attempt in range(3):  # 최대 3회 시도 (분당 제한 대응)
        try:
            resp = requests.post(
                "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent",
                headers={"Content-Type": "application/json", "X-goog-api-key": GEMINI_API_KEY},
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0.3, "responseMimeType": "application/json"}
                },
                timeout=60
            )
            if resp.status_code == 429:
                # 일일 할당량 소진 여부 확인 (재시도해도 소용없음)
                try:
                    err_msg = resp.json().get("error", {}).get("message", "").lower()
                except Exception:
                    err_msg = ""
                if "quota" in err_msg or "resource" in err_msg or attempt >= 1:
                    print(f"[!] Gemini 일일 토큰 한도 소진 — 내일 이어서 실행됩니다.")
                    return None  # None = 일일 한도 소진 신호
                # 분당 제한이면 잠시 대기 후 재시도
                wait = 30 * (attempt + 1)
                print(f"[!] Rate limit (429) — {wait}초 후 재시도 ({attempt+1}/3)...")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
            result = json.loads(text)
            print("[✓] AI 요약 생성 완료")
            return result
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 429:
                print(f"[!] Gemini 일일 토큰 한도 소진 — 내일 이어서 실행됩니다.")
                return None
            print(f"[!] AI 요약 실패: {e}")
            return {}
        except Exception as e:
            print(f"[!] AI 요약 실패: {e}")
            return {}
    print("[!] AI 요약 실패: 재시도 한도 초과")
    return {}


# ── 7. 저장 ─────────────────────────────────────────────────────
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


# ── 8. Google Sheets ─────────────────────────────────────────────
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

    # 원문 시트
    if target_date in existing_titles:
        ss.del_worksheet(ss.worksheet(target_date))
    ws = ss.add_worksheet(title=target_date, rows=300, cols=10, index=0)
    rows = [["작성일시", "매장명", "내용", "게시글ID"]]
    for p in posts:
        rows.append([p["createdAt"], p["store"], p["content"], p["id"]])
    ws.update(range_name="A1", values=rows)
    ws.format("A1:D1", {"textFormat": {"bold": True}})
    print(f"[✓] Google Sheets 원문 시트: {target_date}")

    # 요약 시트
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
        ws2.update(range_name="A1", values=s_rows)
        ws2.format("A1:B1", {"textFormat": {"bold": True}})
        print(f"[✓] Google Sheets 요약 시트: {summary_title}")


# ── 9. 누락 요약 보충 ────────────────────────────────────────────
def summarize_missing(from_date: str = None, to_date: str = None):
    """posts.json에 있지만 summaries.json에 없는 날짜를 순서대로 AI 요약.
    일일 토큰 한도 초과(429) 시 그 즉시 종료 — 내일 이어서 실행.
    from_date/to_date 지정 시 해당 범위만 처리.
    """
    if not os.path.exists(DATA_FILE):
        print("[!] posts.json 없음 — 먼저 데이터 수집을 실행하세요.")
        return

    with open(DATA_FILE, "r", encoding="utf-8") as f:
        all_posts = json.load(f)

    existing_summaries = {}
    if os.path.exists(SUMMARY_FILE):
        with open(SUMMARY_FILE, "r", encoding="utf-8") as f:
            existing_summaries = json.load(f)

    # 요약이 없는 날짜 목록 (오래된 날짜 순)
    missing_dates = sorted([
        d for d in all_posts
        if d not in existing_summaries and all_posts[d]
    ])

    # 날짜 범위 필터
    if from_date:
        missing_dates = [d for d in missing_dates if d >= from_date]
    if to_date:
        missing_dates = [d for d in missing_dates if d <= to_date]

    if not missing_dates:
        print("[✓] 모든 날짜의 AI 요약이 이미 존재합니다.")
        return

    print(f"[>] 요약 미생성 날짜: {len(missing_dates)}일 ({missing_dates[0]} ~ {missing_dates[-1]})")
    print("[!] 일일 토큰 한도 초과 시 자동 종료 — 내일 이어서 실행됩니다.")

    done = 0
    for date_str in missing_dates:
        posts = all_posts[date_str]
        print(f"[>] {date_str} AI 요약 생성 중... ({len(posts)}건)")
        summary = generate_summary(posts, date_str)
        if summary is None:
            # 일일 토큰 한도 소진 — 더 이상 시도 불필요
            print(f"[!] {date_str} 요약 중단 — 일일 한도 소진. 내일 이어서 실행됩니다.")
            break
        if not summary:
            # 기타 오류 — 해당 날짜 건너뛰고 계속
            print(f"[!] {date_str} 요약 오류 — 건너뛰고 다음 날짜로...")
            continue
        save_summary(date_str, summary)
        update_google_sheets(posts, summary, date_str)
        done += 1
        time.sleep(2)  # API 부하 방지

    print(f"[✓] 오늘 요약 완료: {done}일 / 남은 미완료: {len(missing_dates) - done}일")


# ── 메인 ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    args = sys.argv[1:]

    if "--all" in args:
        # 전체 기간 수집 (최초 1회용 — 데이터만, AI 요약 없음)
        session = login()
        all_data = fetch_all_posts(session)
        save_posts(all_data)
        print("[!] 전체 수집 완료. AI 요약은 --summarize 로 별도 실행하세요.")

    elif "--summarize" in args:
        # 누락 AI 요약 보충 (로그인 불필요 — posts.json 로컬 파일만 사용)
        from_date = None
        to_date = None
        if "--from" in args:
            from_idx = args.index("--from")
            from_date = args[from_idx + 1]
        if "--to" in args:
            to_idx = args.index("--to")
            to_date = args[to_idx + 1]
        summarize_missing(from_date, to_date)

    elif "--from" in args:
        # 기간 수집: --from YYYY-MM-DD --to YYYY-MM-DD (AI 요약 없이 원문만)
        session = login()
        from_idx = args.index("--from")
        from_date = args[from_idx + 1]
        to_date = datetime.now(KST).strftime("%Y-%m-%d")  # 기본: 오늘
        if "--to" in args:
            to_idx = args.index("--to")
            if to_idx + 1 < len(args) and args[to_idx + 1]:
                to_date = args[to_idx + 1]
        fetch_posts_for_range(session, from_date, to_date)

    elif args and not args[0].startswith("--"):
        # 단일 날짜 수집: python scrape.py YYYY-MM-DD
        session = login()
        target_date = args[0]
        posts = fetch_posts_for_date(session, target_date)
        if posts:
            save_posts({target_date: posts})
            summary = generate_summary(posts, target_date)
            save_summary(target_date, summary)
            update_google_sheets(posts, summary, target_date)
            print(f"[✓] {target_date}: {len(posts)}건 수집 완료")
        else:
            print(f"[−] {target_date}: 게시글 없음")

    else:
        # 기본: 어제 날짜 수집 (매일 오전 7시 KST 자동실행)
        session = login()
        yesterday = (datetime.now(KST) - timedelta(days=1)).strftime("%Y-%m-%d")
        posts = fetch_posts_for_date(session, yesterday)
        if posts:
            save_posts({yesterday: posts})
            summary = generate_summary(posts, yesterday)
            save_summary(yesterday, summary)
            update_google_sheets(posts, summary, yesterday)
            print(f"[✓] {yesterday}: {len(posts)}건 수집 완료")
        else:
            print(f"[−] {yesterday}: 게시글 없음")
