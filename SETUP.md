# 몬쉘 판매일보 자동화 — 설정 가이드

## 구조

```
moncher-sales-report/
├── .github/workflows/daily-scrape.yml  ← GitHub Actions (자동 실행)
├── scraper/
│   ├── scrape.py                        ← 스크래퍼 + Google Sheets 업데이트
│   └── requirements.txt
├── data/posts.json                      ← 수집된 데이터 (자동 누적)
└── docs/index.html                      ← 웹 대시보드 (GitHub Pages)
```

---

## STEP 1 — GitHub 저장소 생성

1. https://github.com/new 접속
2. Repository name: `moncher-sales-report`
3. **Private** 선택 (판매 데이터이므로 비공개 권장)
4. "Create repository" 클릭

---

## STEP 2 — 파일 업로드

터미널(Git Bash)에서:

```bash
cd moncher-sales-report
git init
git add .
git commit -m "초기 커밋"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/moncher-sales-report.git
git push -u origin main
```

---

## STEP 3 — GitHub Secrets 등록

저장소 → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

| Secret 이름 | 값 |
|---|---|
| `DAOU_ID` | `juyoo` |
| `DAOU_PW` | (DaouOffice 비밀번호) |
| `SPREADSHEET_ID` | (Google Sheets URL의 `/d/` 뒤 ID) |
| `GOOGLE_CREDENTIALS_JSON` | (아래 STEP 4 참고) |

---

## STEP 4 — Google Sheets 연동 (선택)

### 4-1. Google Cloud 설정

1. https://console.cloud.google.com 접속
2. 새 프로젝트 생성 (예: `moncher-scraper`)
3. **API 및 서비스** → **라이브러리** → "Google Sheets API" 검색 → 사용 설정
4. **API 및 서비스** → **사용자 인증 정보** → **서비스 계정 만들기**
   - 이름: `moncher-scraper`
   - 역할: 편집자
5. 서비스 계정 클릭 → **키** 탭 → **키 추가** → JSON → 다운로드

### 4-2. Sheets 공유

1. 사용할 Google 스프레드시트 열기
2. URL에서 ID 복사: `https://docs.google.com/spreadsheets/d/[여기]/edit`
3. **공유** 버튼 → 서비스 계정 이메일 추가 (편집자 권한)
   - 이메일 형식: `moncher-scraper@프로젝트명.iam.gserviceaccount.com`

### 4-3. Secret 등록

다운로드한 JSON 파일 전체 내용을 `GOOGLE_CREDENTIALS_JSON` Secret에 붙여넣기

---

## STEP 5 — GitHub Pages (웹 대시보드) 활성화

1. 저장소 → **Settings** → **Pages**
2. Source: **Deploy from a branch**
3. Branch: `main` / Folder: `/docs`
4. Save

→ 몇 분 후 `https://YOUR_USERNAME.github.io/moncher-sales-report/` 에서 대시보드 확인

---

## 실행 일정

- **자동**: 매일 오전 7시 KST (GitHub 서버에서 자동 실행 — 컴퓨터 꺼져 있어도 OK)
- **수동**: 저장소 → **Actions** → **판매일보 자동 수집** → **Run workflow**
  - 날짜 입력란에 `2026-05-03` 형식으로 입력하면 특정 날짜 재수집 가능

---

## 문제 해결

| 증상 | 원인 | 해결 |
|---|---|---|
| 로그인 실패 | 비밀번호 변경 | `DAOU_PW` Secret 업데이트 |
| Sheets 미업데이트 | 서비스 계정 미공유 | Sheets에 이메일 공유 재확인 |
| 웹 대시보드 빈 화면 | Pages 미활성화 | STEP 5 재확인 |
| 게시글 0건 | 해당 날짜 미작성 | 정상 (빈 날짜는 저장 안 됨) |
