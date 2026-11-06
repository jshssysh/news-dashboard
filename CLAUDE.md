# news-dashboard 작업 규칙

## 답변
- 답변과 버튼 문구는 모두 한국어로 쓴다.

## push (회사 PC)
- 이 PC에서는 Claude 세션, 터미널 패널, GitHub Desktop 모두 GitHub 로그인이 안 된다.
  커밋은 Claude가 만들고, push는 사용자가 Git Bash에서 토큰 주소로 직접 한다.
  `push origin`으로 안내하지 않는다. 토큰 값은 채팅으로 받지 않는다.

  ```bash
  git -C "C:\Users\samsung\Documents\GitHub\news-dashboard" push https://ghp_토큰@github.com/jshssysh/news-dashboard.git main:main
  ```

- 한 번에 약 **30KB**가 넘는 업로드는 회사 보안 정책에 막힌다.
  push를 안내하기 전에 원격 main을 받아 보내는 크기를 잰다.

  ```bash
  git fetch -q https://github.com/jshssysh/news-dashboard.git main
  printf 'HEAD\n^FETCH_HEAD\n' | git pack-objects --revs --thin --stdout -q | wc -c
  ```

- 수집 봇이 데이터 커밋을 자주 올리므로, 원격이 앞서 있으면 FETCH_HEAD를 먼저 병합한다
  (데이터 파일과 코드 파일이 겹치지 않아 보통 충돌이 없다).

## 데이터 파일
- `*.csv`, `docs/` 같은 수집 결과는 GitHub Actions(`.github/workflows/daily.yml`)의
  수집 봇이 만들고 커밋한다. 로컬에서 수집한 데이터는 미리보기 확인용으로만 쓰고
  저장소 밖(scratchpad)에 두며 커밋하지 않는다.
- 데이터를 손으로 고쳐야 하면 `config/news_overrides.csv`처럼 서버에서 반영되는 방법을 쓴다.

## 회사망 제약 (로컬 확인할 때)
- Python requests는 회사망 인증서 때문에 SSL 검증에 실패한다. 법제처(law.go.kr)는
  Windows `C:\Windows\System32\curl.exe`로 받으면 된다(인증서 검증은 그대로 둔다).
- 국회 사이트(open.assembly.go.kr, likms.assembly.go.kr)는 Claude 도구에서 막혀 있다.
  사용자 브라우저에서는 열린다. 국회 API 응답은 Actions에서 확인한다
  (`.github/workflows/probe-assembly-api.yml`).
- 화면 반영은 push만으로 안 되고 워크플로가 `docs/`를 다시 만들어야 한다
  (아래 "워크플로 수동 실행 안내"의 ① 화면만 다시 만들기).
- github.com(Actions 로그 화면)은 Claude 브라우저에서 막혀 있다. 실행 여부는
  `curl.exe -s https://api.github.com/repos/jshssysh/news-dashboard/actions/runs?per_page=5`로,
  결과는 커밋된 파일로 확인한다(로그 다운로드는 로그인이 필요해 안 된다).

## 워크플로 수동 실행 안내
- push 뒤 워크플로를 손으로 돌려야 하면, 경로와 체크 표를 **매번 아래 모양 그대로** 보여 준다.
  입력 9개를 GitHub 화면 순서대로 모두 적고, 체크할 칸만 ✅, 나머지는 "그대로",
  일부러 체크하면 안 되는 칸은 **체크 안 함**으로 적는다.
- 경로: GitHub 저장소 **Actions** 탭 → 왼쪽 **"[수집] 데이터 수집·화면 갱신"** →
  오른쪽 **Run workflow** → 표대로 체크 → 초록색 **Run workflow** 버튼.

| 입력 | ① 화면만 다시 만들기 | ② 법령 지금 수집 | ③ 심·판결 지금 수집 | ④ 법안만 다시 받기 |
|---|---|---|---|---|
| skip_news | ✅ | ✅ | ✅ | ✅ |
| skip_ai | 그대로 | 그대로 | 그대로 | 그대로 |
| skip_bills | ✅ | ✅ | ✅ | **체크 안 함** |
| skip_member_news | ✅ | ✅ | ✅ | ✅ |
| skip_decisions | ✅ | ✅ | **체크 안 함** | ✅ |
| skip_law_penalties | ✅ | **체크 안 함** | ✅ | ✅ |
| skip_decisions_wait | 그대로 | 그대로 | ✅ (지금 바로 수집) | 그대로 |
| skip_law_penalties_wait | 그대로 | ✅ (지금 바로 수집) | 그대로 | 그대로 |
| decisions_lookback_days | 비워 둠 | 비워 둠 | 비워 둠 | 비워 둠 |

- 언제 무엇을 쓰나
  - ① 화면만 다시 만들기: `html_template.html`·`generate_html.py`를 고친 뒤. 1~2분.
  - `config/news_overrides.csv`(기사 이슈 바꾸기·삭제)는 `main.py`가 돌 때만 반영되므로 ①로는 안 바뀐다.
    손으로 돌리지 않고 다음 정기 실행(06:01/12:01/18:01)을 기다린다.
  - ② 법령 지금 수집: 법령 쪽 수집기(`collect_law_*.py`, `collect_subordinate_laws.py`,
    `collect_legislative_notices.py`)를 고친 뒤. 법령 벌칙, 공포·시행, 조문·행정규칙, 입법예고를 모두 돈다.
    본문을 새로 받아야 하면 20분 넘게 걸린다.
  - ③ 심·판결 지금 수집: `collect_decisions.py`를 고친 뒤.
  - ④ 법안만 다시 받기: `collect_bills.py`를 고친 뒤.
  - 뉴스 수집(`main.py`)을 고친 것은 손으로 돌리지 않고 다음 정기 실행(06:01/12:01/18:01)을 기다린다
    (Gemini 무료 한도를 아끼려고).
- 표 안내 뒤에는 "끝나면 알려 주세요"라고 하고, 끝났다고 하면 커밋된 결과 파일로 확인한다.
