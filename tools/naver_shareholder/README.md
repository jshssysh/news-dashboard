# 주요 인사 주주보호·자본시장 발언 기사 후보 수집 (임시 도구)

네이버 뉴스 검색 API로 7인 × 이슈 키워드 26개(검색어 182개)를 검색해
'워딩 정리' 시트에 병합할 **후보 목록** 엑셀(`naver_후보_YYMMDD.xlsx`)을 만든다.
대시보드 수집(`src/`)과 관계없는 일회성 도구이며, 결과 파일은 저장소에 커밋하지 않는다.
다 쓰면 이 폴더와 `.github/workflows/naver-shareholder-once.yml`을 지우면 된다.

## 1. GitHub Actions에서 돌리기 (권장)

키는 저장소 Secrets의 `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET`(정기 뉴스 수집과 같은 키)을 쓴다.

1. 저장소 **Actions** 탭 → 왼쪽 **"[임시] 주주보호 발언 기사 후보 수집"** → **Run workflow**
2. 입력
   - `start`: 시작일(기본 2026-01-01)
   - `end`: 종료일, 비우면 오늘(KST)
   - `limit_queries`: 시험할 때만 숫자(예: 3), 전체는 비워 둠
3. 끝나면 실행 화면 아래 **Artifacts**의 `naver-candidates`를 내려받는다(7일 보관).
   요약은 실행 화면의 Summary에 나온다.

정기 수집(06:01/12:01/18:01)과 같은 키·일일 한도를 쓰므로 그 시간대는 피한다.
전체 실행은 최대 약 1,820회 호출이다.

## 2. 기존 엑셀과 대조 (로컬)

기존 파일은 저장소에 올리지 않으므로, Actions 결과에는 `기존반영`이 비어 있다.
내려받은 파일과 기존 엑셀을 같은 폴더에 두고 로컬에서 대조만 돌린다(API 호출 없음).

```
pip install -r requirements.txt
python collect_naver_news.py --compare-only naver_후보_261125.xlsx --existing 261125_주요인사_주주보호_자본시장_워딩_정리.xlsx
```

`naver_후보_261125_대조.xlsx`로 새로 저장한다(원본 두 파일은 바꾸지 않음).

## 3. 로컬에서 직접 수집할 때

같은 폴더에 `.env`를 만든다(`.gitignore`에 들어 있어 커밋되지 않음).

```
NAVER_API_MODE=developers
NAVER_CLIENT_ID=발급받은_ID
NAVER_CLIENT_SECRET=발급받은_SECRET
```

```
python collect_naver_news.py --dry-run                         # 검색어 목록·예상 호출 수만
python collect_naver_news.py --limit-queries 3                 # 시험
python collect_naver_news.py --start 2026-01-01 --end 2026-11-25
```

기존 엑셀이 현재 폴더에 있거나 `--existing`으로 주면 수집하면서 바로 대조한다.
회사망에서는 Python의 SSL 검증이 실패할 수 있어 Actions 실행을 권한다.

## 모드 전환

`NAVER_API_MODE`는 현재 `developers`(네이버 개발자센터)만 지원한다.
`apihub`(NAVER API HUB)는 뉴스 검색 경로를 공식 문서에서 확인한 뒤
`collect_naver_news.py`의 `API_URLS`에 경로를 넣고, 인증 헤더를
`X-NCP-APIGW-API-KEY-ID` / `X-NCP-APIGW-API-KEY`로 바꾸도록 `load_keys()`를 고쳐야 한다.

## 처리 내용

- 최신순(`sort=date`)으로 100건씩, 검색어당 최대 1,000건. 시작일 이전 기사가 나오면 그 검색어는 멈춤
- 필터: 기간 → 인물(동명이인 단어) → 관련성(이슈 키워드). 키워드는 띄어쓰기 차이도 잡고,
  영문 키워드(IR 등)는 영단어 안에 섞인 경우를 뺀다
- 중복: 원문링크 같으면 1건(검색어 칸에 걸린 검색어를 모두 적음),
  같은 날짜·발언자에서 제목 유사도 0.85 이상이면 묶고 `동일보도_건수`에 수를 적음
- `이슈`: 제목·요약에 나온 이슈 키워드 목록. `영향 항목(추정)`·`영향도(추정)`: 태깅 규칙 중 가장 위 1개
- 오류: 429·5xx는 1·2·4초 간격으로 3회 재시도, 400은 그 검색어를 건너뛰고 기록, 401·403은 즉시 중단
- 기사 본문은 받지 않는다(API 필드와 링크만 저장)
