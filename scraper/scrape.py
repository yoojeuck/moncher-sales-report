"""
몬쉘 판매일보 스크래퍼
DaouOffice에서 판매일보 게시글을 가져와 JSON 저장 + Google Sheets 업데이트
"""

import os
import json
import re
import sys
from datetime import datetime, timezone, timedelta

import requests

# ── 설정 ──────────────────────────────────────────────────────────
BASE_URL    = "https://monchouchou.daouoffice.com"
COMPANY_ID  = "5000001396"
BOARD_ID    = "17422"
LOGIN_ID    = os.environ.get("DAOU_ID", "juyoo")
PASSWORD    = os.environ.get("DAOU_PW", "")
KST         = timezone(timedelta(hours=9))
DATA_FILE   = os.path.join(os.path.dirname(__file__), "..", "data", "posts.json")


# ── 1. DaouOffice 로그인 ──────────────────────────────────────────
def login() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Content-Type": "application/json",
        "X-Referer-Info": "monchouchou.daouoffice.com",
        "Referer": f"{BASE_URL}/login",
        "Origin": BASE_URL,
    })
    resp = session.post(f"{BASE_URL}/api/portal/public/auth/login", json={
        "companyId": COMPANY_ID,
        "loginId": LOGIN_ID,
        "password": PASSWORD,
        "captcha": "",
        "locale": "ko",
    })
    resp.raise_for_status()
    token = session.cookies.get("AccessToken")
    if not token:
        raise RuntimeError("로그인 실패: AccessToken을 받지 못했습니다.")
    session.headers["Authorization"] = f"Bearer {token}"
    print(f"[✓] 로그인 성공")
    return session


# ── 2. 오늘 게시글 목록 가져오기 ─────────────────────────────────
def fetch_today_posts(session: requests.Session, target_date: str | None = None) -> list[dict]:
    """
    target_date: 'YYYY-MM-DD' 형식. None이면 오늘(KST) 기준.
    """
    if target_date is None:
        target_date = datetime.now(KST).strftime("%Y-%m-%d")

    print(f"[>] {target_date} 게시글 수집 중...")

    all_posts = []
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
            # KST 날짜 비교 (API 응답이 이미 +09:00 포함)
            post_date = created[:10] if created else ""
            if post_date == target_date:
                all_posts.append({
                    "id":         post.get("id"),
                    "date":       post_date,
                    "createdAt":  created,
                    "store":      post.get("writer", {}).get("name", ""),
                    "content":    post.get("summary", "").strip(),
                    "writer_id":  post.get("writerId"),
                })
            elif post_date < target_date:
                # 날짜 역순 정렬이므로 더 오래된 글이 나오면 종료
                return all_posts

        if not data.get("hasNext", False):
            break
        page += 1

    return all_posts


# ── 3. JSON 파일에 날짜별로 저장 ─────────────────────────────────
def save_to_json(posts: list[dict], target_date: str):
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)

    # 기존 데이터 로드
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            all_data = json.load(f)
    else:
        all_data = {}

    all_data[target_date] = posts

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2)

    print(f"[✓] JSON 저장: {DATA_FILE} ({target_date}, {len(posts)}건)")


# ── 4. Google Sheets 업데이트 ─────────────────────────────────────
def update_google_sheets(posts: list[dict], target_date: str):
    """
    GOOGLE_CREDENTIALS_JSON 환경변수(서비스 계정 JSON 문자열) 필요
    SPREADSHEET_ID 환경변수 필요
    """
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    spreadsheet_id = os.environ.get("SPREADSHEET_ID")

    if not creds_json or not spreadsheet_id:
        print("[!] Google Sheets 환경변수 미설정 — 건너뜁니다.")
        return

    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        print("[!] gspread / google-auth 미설치 — 건너뜁니다.")
        return

    creds_dict = json.loads(creds_json)
    scopes = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    gc = gspread.authorize(creds)
    ss = gc.open_by_key(spreadsheet_id)

    sheet_title = target_date  # 예: "2026-05-04"

    # 시트가 이미 있으면 삭제 후 재생성 (덮어쓰기)
    existing = [ws.title for ws in ss.worksheets()]
    if sheet_title in existing:
        ss.del_worksheet(ss.worksheet(sheet_title))

    # 새 시트를 index=0 (가장 왼쪽) 으로 추가
    ws = ss.add_worksheet(title=sheet_title, rows=200, cols=10, index=0)

    # 헤더
    headers_row = ["작성일시", "매장명", "내용", "게시글ID"]
    rows = [headers_row]
    for p in posts:
        rows.append([p["createdAt"], p["store"], p["content"], p["id"]])

    ws.update("A1", rows)

    # 헤더 굵게
    ws.format("A1:D1", {"textFormat": {"bold": True}})
    # 내용 열(C) 너비 자동 조정 요청은 gspread 한계로 생략

    print(f"[✓] Google Sheets 업데이트: '{sheet_title}' 시트 생성 ({len(posts)}행)")


# ── 메인 ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else None  # 인수로 날짜 지정 가능

    session = login()
    posts = fetch_today_posts(session, target)

    if not posts:
        date_str = target or datetime.now(KST).strftime("%Y-%m-%d")
        print(f"[!] {date_str} 에 등록된 게시글이 없습니다.")
        sys.exit(0)

    date_str = posts[0]["date"]
    print(f"[✓] {len(posts)}건 수집 완료")

    save_to_json(posts, date_str)
    update_google_sheets(posts, date_str)

    print("[✓] 완료!")
