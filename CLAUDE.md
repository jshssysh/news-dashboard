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
- 화면 반영은 push만으로 안 되고 워크플로가 `docs/`를 다시 만들어야 한다.
  바로 보려면 Run workflow에서 skip_news, skip_bills, skip_member_news,
  skip_decisions, skip_law_penalties를 체크해 화면만 다시 만든다.
