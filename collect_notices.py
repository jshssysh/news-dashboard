"""
법제처 국가법령정보 Open API(target=admrul, "행정규칙")에서 공정거래위원회가
발령한 고시(훈령/예규 등 다른 행정규칙 종류는 제외 - 사용자가 요청한 범위)
전체 목록을 받아 notice_list.csv로 저장한다.

법령(법률/시행령/시행규칙)과 행정규칙(훈령/예규/고시/공고/지침)은 법제처
API에서 완전히 다른 서비스(target=law류 vs target=admrul)라, 시행령/
시행규칙은 이미 collect_law_penalties.py가 "법령"으로 분류해 가져오고
있었지만(법령구분="대통령령" 등) 고시는 지금까지 아예 호출된 적이 없었다
(2026-09-29 사용자 확인).

decision_list.csv(의결서/판례)처럼 사라지지 않는 기록을 계속 쌓는 게 아니라,
bill_list.csv처럼 "지금 유효한 고시 목록"을 매번 통째로 새로 받아 덮어쓴다 -
고시가 폐지되면 목록에서 사라져야 자연스럽기 때문이다(법제처 API가
기본적으로 "현행연혁구분=현행"인 것만 돌려주는 것으로 실측 확인됨).

사용자 요청 범위: "벌칙류 조문만 골라내는" collect_law_penalties.py 방식이
아니라, 공정위 고시 전체 목록(제목/발령일/시행일/제개정구분 + 원문 링크)을
그대로 보여준다 - 고시는 대부분 기준/지침/공시의무를 정하는 것이라 벌칙
조문 자체가 거의 없어서, 벌칙류로 좁히면 결과가 사실상 0건에 가깝기 때문
(2026-09-29 API 실측: knd=3(고시)로 org=1130000 조회 시 91건, 표본 5건
모두 벌칙 조문과 무관한 기준/지침/양식류였음).

사용 API (www.law.go.kr/DRF, LAW_GO_KR_OC 필요 - collect_decisions.py와 동일):
- lawSearch.do?target=admrul&org=1130000&knd=3: 공정위(org=1130000) 소관
  행정규칙 중 종류가 고시(knd=3)인 것만 목록 조회. 이름/발령일/시행일/
  제개정구분/상세링크가 목록 API 한 번으로 전부 나와서 본문 조회
  (lawService.do)까지는 필요 없다 - AI 요약도 안 붙이므로 Gemini 호출도 없다.

법 개정처럼 고시도 일주일에 몇 건 안 나오므로(collect_decisions.py/
collect_law_penalties.py와 같은 이유), daily.yml처럼 하루 5번씩 돌 필요가
없다 - main()이 토요일 06시대 실행일 때만 실제로 수집한다.

실행: python collect_notices.py
"""
import os
import time
import traceback
from datetime import datetime, timedelta, timezone

import requests
import pandas as pd

LAW_API_OC = os.environ.get("LAW_GO_KR_OC", "").strip() or "test"
KST = timezone(timedelta(hours=9))

NOTICE_LIST_PATH = "notice_list.csv"
LAW_API_BASE = "https://www.law.go.kr/DRF"
# "상세링크"에 OC를 직접 박지 않는다 - collect_decisions.py와 같은 이유(2026-10-01
# 보안 리뷰로 발견: OC를 그대로 저장하면 공개 저장소/GitHub Pages에 실제 인증키가
# 노출된다). Worker의 GET /link가 자신의 시크릿으로 실제 링크를 만들어 리디렉션한다.
WORKER_LINK_BASE = "https://news-dashboard-contract-checker.jshssysh.workers.dev/link"

FTC_ORG_CODE = "1130000"
NOTICE_KND_CODE = "3"  # 행정규칙종류: 1=훈령, 2=예규, 3=고시, 4=공고, 5=지침, 6=기타


def get_law_api_with_retry(url, params, timeout=20, retries=2, retry_wait=10):
    """법제처 API 호출을 감싸서, 접속 실패(타임아웃 등)면 짧게 대기 후 재시도한다
    (collect_decisions.py/collect_law_penalties.py의 동일 함수와 같은 목적)."""
    last_exc = None
    for attempt in range(retries + 1):
        try:
            res = requests.get(url, params=params, timeout=timeout)
            if res.status_code >= 500 and attempt < retries:
                time.sleep(retry_wait)
                continue
            return res
        except requests.exceptions.RequestException as e:
            last_exc = e
            if attempt < retries:
                time.sleep(retry_wait)
                continue
            raise
    raise last_exc


def fetch_notice_list(max_pages=20):
    """공정위(org=1130000) 소관 고시(knd=3) 전체를 최신순으로 훑는다.
    91건(2026-09-29 실측) 정도라 페이지 한도는 넉넉히 잡아둔다."""
    rows = []
    for page in range(1, max_pages + 1):
        params = {
            "OC": LAW_API_OC, "target": "admrul", "org": FTC_ORG_CODE, "knd": NOTICE_KND_CODE,
            "type": "JSON", "display": 100, "page": page, "sort": "ddes",
        }
        try:
            res = get_law_api_with_retry(f"{LAW_API_BASE}/lawSearch.do", params)
            if res.status_code != 200:
                print(f"[고시 목록 조회 실패] {page}페이지: status={res.status_code} body={res.text[:200]}")
                break
            data = res.json()
            items = data.get("AdmRulSearch", {}).get("admrul", [])
            if not items:
                break
            if isinstance(items, dict):  # 결과가 1건이면 리스트가 아니라 딕셔너리 하나로 옴
                items = [items]
            for item in items:
                rows.append({
                    "행정규칙명": item.get("행정규칙명", ""),
                    "행정규칙종류": item.get("행정규칙종류", ""),
                    "소관부처명": item.get("소관부처명", ""),
                    "발령번호": item.get("발령번호", ""),
                    "발령일자": item.get("발령일자", ""),
                    "시행일자": item.get("시행일자", ""),
                    "제개정구분명": item.get("제개정구분명", ""),
                    "행정규칙ID": item.get("행정규칙ID", ""),
                })
            if len(items) < 100:
                break
        except Exception:
            print(f"[고시 목록 조회 예외] {page}페이지:\n{traceback.format_exc()}")
            break
        time.sleep(0.2)
    return rows


def main():
    # 고시도 의결서/판례/법령 벌칙 조문처럼 일주일에 몇 건 안 나온다 - 주 1회,
    # 토요일 06:01 실행 때만 실제로 수집하고 나머지 4번(같은 날 07:12/07:47/
    # 12:01/18:01)과 평일은 건너뛴다(collect_decisions.py와 같은 이유·같은 방식).
    # 초기 백필 등으로 지금 당장 한 번 돌리고 싶을 때는 NOTICES_SKIP_WAIT=true로
    # 이 요일 제한을 건너뛸 수 있다.
    skip_wait = os.environ.get("NOTICES_SKIP_WAIT", "").strip().lower() == "true"
    now = datetime.now(KST)
    if not skip_wait and not (now.weekday() == 5 and now.hour == 6):
        print(f"[고시 수집] 주 1회(토요일 06시대)만 실행하도록 정해둬서, "
              f"이번 실행({now.strftime('%a %H:%M')})은 건너뜁니다.")
        return

    if LAW_API_OC == "test":
        print("[경고] LAW_GO_KR_OC가 설정되지 않아 데모키(test)로 호출합니다 - "
              "체험용이라 정식 서비스에는 부적절하니 법제처에 정식 OC를 발급받아 "
              "시크릿으로 등록하세요(신청/문의 02-2109-6446).")

    rows = fetch_notice_list()
    if not rows:
        print("[고시 수집] 조회된 고시가 없습니다(API 오류 또는 실제로 0건).")
        return

    now_str = now.strftime("%Y-%m-%d %H:%M")
    for row in rows:
        row["상세링크"] = f"{WORKER_LINK_BASE}?target=admrul&id={row['행정규칙ID']}"
        row["최종수집일"] = now_str

    df = pd.DataFrame(rows).sort_values("발령일자", ascending=False)
    df.to_csv(NOTICE_LIST_PATH, index=False, encoding="utf-8-sig")
    print(f"[고시 수집 완료] {len(df)}건 저장")


if __name__ == "__main__":
    main()
