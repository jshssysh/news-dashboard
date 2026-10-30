"""
국회 열린국회정보 Open API에서 국회 전체 계류 법안을 수집해 bill_list.csv로 저장한다.

사용 API (open.assembly.go.kr, ASSEMBLY_API_KEY 필요):
- 계류의안(nwbqublzajtcqpdae): 전체 계류 법안 목록. 소관위/법사위 처리 단계 필드가
  응답에 이미 포함돼 있어서, 법안마다 상세조회를 따로 할 필요가 없다. 본회의 회부를
  직접 알려주는 날짜 필드는 없지만, 법사위 결과가 원안가결/수정가결이면 본회의
  표결 대기로 넘어간 것으로 간접 판단한다(derive_stage_and_result 참고). 정부
  이송/공포는 이 API에 아예 없음 - "계류"(아직 안 끝난) 법안만 주는 API라 그
  단계까지 간 법안은 여기 안 나온다.
- 본회의 처리안건 - 법률안(nwbpacrgavhjryiph): 본회의에서 처리된 법률안의 의결일
  (RGS_PROC_DT)·정부이송일(CURR_TRANS_DT)·공포일(ANNOUNCE_DT). 이 중 가결된 것을
  "공포" 단계로 목록에 더한다(공포일이 아직 없으면 결과 "정부이송"). 철회도
  RGS_PROC_DT가 찍혀 오므로(실측) 결과에 "가결"이 든 것만 쓴다.
- 처리의안(nzpltgfqabtcpsmai): 처리가 끝난 의안. 계류의안과 필드가 같아서(상임위·
  법사위 결과 포함) 위 본회의 처리안건과 의안ID로 묶어 상임위·법사위 단계 정보를
  채운다. 두 서비스 ID와 필드는 probe_assembly_api.py를 Actions에서 돌려 확인함
  (2026-10-27).
- 국회의원 인적사항(ALLNAMEMBER): 대표발의자 이름으로 정당/선수/지역구를 찾아서
  "제안자" 표시 문구에 괄호로 덧붙인다 (예: "황운하의원(조국혁신당, 재선, 비례대표) 등 10인").

법안명이 config/bill_keywords.yaml에 있는 법률명을 포함하면 그 카테고리로,
아니면 "기타"로 분류한다 (더 이상 검색 필터로는 안 씀 - 전체를 다 가져온 뒤 분류만 함).

API 자체엔 제안이유/주요내용(법안 본문) 필드가 없지만, likms.assembly.go.kr의
"제안이유 요약" 팝업 페이지(billSummary.do)는 자바스크립트 없이도 해당 텍스트를
그대로 담고 있어서, 이 페이지를 긁어와 Gemini로 한 줄 요약해 붙인다. 전체 법안이
수만 건이라 전부 요약하면 무료 등급 한도를 하루에 다 써버리므로, 최근
RECENT_DAYS_FOR_SUMMARY일 이내 발의된 것만 요약 대상으로 한다.
한 번 요약된 법안은 다음 실행부터 재사용한다(제안이유 자체는 안 바뀌므로).

bill_list.csv는 매번 통째로 새로 쓰고 수집이 하루 여러 번 돌아서, "상태변경"
칸은 직전 실행 대비 변동만 담고 몇 시간 뒤 지워진다. 그래서 신규 발의·상태변경·
목록 제외(본회의 통과/철회/폐기 등으로 계류의안에서 빠짐)를 감지한 시각과 함께
bill_status_log.csv에 덧붙여 쌓는다 - 주간·월간 변동은 이 파일에서 뽑는다.
실행: python collect_bills.py
"""
import json
import os
import re
import time
import traceback
from collections import Counter
import requests
import yaml
import pandas as pd
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone

ASSEMBLY_API_KEY = os.environ.get("ASSEMBLY_API_KEY", "").strip().replace('"', '').replace("'", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip().replace('"', '').replace("'", "")
KST = timezone(timedelta(hours=9))

BILL_KEYWORDS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config", "bill_keywords.yaml")
BILL_VETOED_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config", "bill_vetoed.csv")
BILL_LIST_PATH = "bill_list.csv"
BILL_STATUS_LOG_PATH = "bill_status_log.csv"
BILL_STATUS_LOG_COLUMNS = ["감지일시", "구분", "의안ID", "의안번호", "법안명", "소관위원회", "이전상태", "새상태"]
MEMBER_LIST_PATH = "member_list.csv"

# 인적사항 API는 역대 의원 3,300명을 모두 주는데, 이 대시보드는 22대 계류법안만
# 다루므로 현역만 남긴다. 당선 대수 칸(GTELT_ERACO)이 "제21대, 제22대" 형태다.
CURRENT_ERA = "제22대"
CURRENT_ERA_NO = "22"

# ALLNAMEMBER는 인적사항 "이력"만 주고 지금 실제로 의석을 지키고 있는지는
# 안 알려준다 - 궐원/사퇴 여부를 알려주는 API를 찾지 못했다. 그래서 지자체장
# 당선 등으로 국회의원과 겸직 금지가 걸려 의원직을 내려놓은 사람도 API에는
# 한동안(계속) "22대 현역"으로 남는다.
#
# 2026-06-03 제9회 전국동시지방선거에서 당선돼 의원직을 사퇴한 사람들 -
# 당선자 명단(위키백과)과 news_list.csv 실제 보도(또는 취임 보도 검색)로
# 하나하나 대조해서 확인함. 앞으로 비슷한 경우가 생기면 이름만 추가하면 된다.
# 지자체장 등으로 옮겨서(국회의원과 겸직 금지) 사실상 의정활동을 안 하는
# 사람들 - API에는 여전히 22대 현역으로 남아 있으므로(위 주석 참고), 명단에서
# 빼는 대신 "現 OOO" 처럼 지금 맡은 자리를 알 수 있게 표시만 한다.
# 값은 카드에 그대로 "現 {값}"으로 찍히는 짧은 직함이다.
MEMBER_ROLE_CHANGES = {
    "추미애": "경기도지사",
    "민형배": "전남광주통합특별시장",
    "전재수": "부산광역시장",
    "추경호": "대구광역시장",
    "박찬대": "인천광역시장",
    "김상욱": "울산광역시장",
    "박수현": "충청남도지사",
    "이원택": "전북특별자치도지사",
    "위성곤": "제주특별자치도지사",
    # 2025-06 취임. 대통령도 국회의원과 겸직 금지 대상이라 같은 문제가 생긴다.
    "이재명": "대통령",
    # 대통령실/행정부로 옮긴 경우 - 전부 겸직 금지로 의원직 사퇴가 필요했다.
    "강훈식": "대통령비서실장",
    "위성락": "국가안보실장",
    "임광현": "국세청장",
    "정을호": "대통령비서실 정무비서관",
    "강유정": "대통령실 수석대변인",
    # 조국은 의원직 상실(아래 MEMBER_SEAT_LOST) 뒤 당대표를 거쳐 당 싱크탱크
    # 원장으로 자리를 옮겼다 - 지금 맡은 자리가 있으니 여기 둔다.
    "조국": "조국혁신당 혁신정책연구원장",
    # 자진 사퇴(12·3 계엄 사태에 반발, 본업인 의사로 복귀) - 지자체장 등과
    # 사유는 다르지만 "지금 의원이 아니고 이걸 하고 있다"는 성격은 같다.
    "인요한": "의원직 사퇴 · 의사 복귀",
}

# 위 MEMBER_ROLE_CHANGES와 달리 "새 자리로 옮긴" 게 아니라 확정판결로 의원직
# 자체를 잃은 경우다 - 성격이 달라 카드에도 다른 배지로 구분해서 보여준다.
# 값은 배지에 "의원직 상실, {값}"으로 그대로 찍히므로 몇 글자로 짧게 줄인다
# (날짜·"확정판결" 같은 상세는 뺀다). 전부 법원 판결이 여러 언론에 보도된
# 공개된 사실만 담는다.
MEMBER_SEAT_LOST = {
    "권성동": "정치자금법 위반",
    "신영대": "당선무효(선거사무장 유죄)",
    "이병진": "재산신고 누락",
    "양문석": "대출사기 확정판결",
}

BASE_URL = "https://open.assembly.go.kr/portal/openapi"
PENDING_BILL_API = "nwbqublzajtcqpdae"  # 계류의안
PLENARY_BILL_API = "nwbpacrgavhjryiph"  # 본회의 처리안건 - 법률안 (의결일·정부이송일·공포일)
PROCESSED_BILL_API = "nzpltgfqabtcpsmai"  # 처리의안 (계류의안과 같은 필드)
MEMBER_INFO_API = "ALLNAMEMBER"  # 국회의원 인적사항 (정당/선수/지역구)
COMMITTEE_MEMBER_API = "nktulghcadyhmiqxi"  # 위원회 위원 명단 (JOB_RES_NM에 '위원장'/'간사'/'위원' 구분이 있음)
BILL_SUMMARY_URL = "https://likms.assembly.go.kr/bill/bi/popup/billSummary.do"
LIKMS_BILL_STATUS_URL = "https://likms.assembly.go.kr/bill/bi/common/findBillDetail.do"

# 전체 법안이 수만 건이라, 이 기간 이내 발의된 것만 AI 한줄요약을 만든다
# (오래된 대기 법안은 법안명/상태/링크만 표시되고 요약은 "준비 중"으로 남음)
RECENT_DAYS_FOR_SUMMARY = 30


def post_gemini_with_retry(url, payload, timeout=30, retries=1, retry_wait=5):
    """Gemini 호출을 감싸서, 서버 과부하(503)나 타임아웃처럼 일시적 오류일 때만
    짧게 대기 후 한 번 더 시도한다 (main.py의 동일 함수와 같은 목적)."""
    last_exc = None
    for attempt in range(retries + 1):
        try:
            res = requests.post(url, json=payload, headers={"x-goog-api-key": GEMINI_API_KEY}, timeout=timeout)
            if res.status_code == 503 and attempt < retries:
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


def load_bill_keywords():
    with open(BILL_KEYWORDS_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)["keywords"]


# html_template.html의 COMMITTEE_ABBR와 동일 - 카테고리 폴백에 쓸 위원회 약칭.
# 국회 인터넷 의사중계 표기를 따른다(2026-10-19 - 산자중기위/기후노동위/재경위).
COMMITTEE_ABBR = {
    "국회운영위원회": "운영위", "법제사법위원회": "법사위", "정무위원회": "정무위",
    "재정경제기획위원회": "재경위", "교육위원회": "교육위", "과학기술정보방송통신위원회": "과방위",
    "외교통일위원회": "외통위", "국방위원회": "국방위", "행정안전위원회": "행안위",
    "문화체육관광위원회": "문체위", "농림축산식품해양수산위원회": "농해수위",
    "산업통상자원중소벤처기업위원회": "산자중기위", "보건복지위원회": "복지위",
    "기후에너지환경노동위원회": "기후노동위", "국토교통위원회": "국토위", "정보위원회": "정보위",
    "성평등가족위원회": "성평등가족위", "예산결산특별위원회": "예결위", "기후위기 특별위원회": "기후위기특위",
}


def committee_abbr(name):
    if not name:
        return name
    if name in COMMITTEE_ABBR:
        return COMMITTEE_ABBR[name]
    return name[:-3] + "위" if name.endswith("위원회") else name


def categorize_bill(bill_name, keywords_map, committee=""):
    """법안명에 공정위 관련 법률명 키워드가 포함되어 있으면 그 카테고리를 반환한다.
    안 걸리면(공정위 관련 법이 아니면) "기타"로 뭉뚱그리지 않고 소관위원회 약칭을
    카테고리로 써서, 국회 전체 법안을 다 가져오는 지금 목록에서 뭘 봐도 "기타"만
    뜨는 일이 없게 한다. 위원회 정보 자체가 없으면(아직 상임위 회부 전) "배정
    안됨"으로 표시 - "기타"라고 하면 공정위 관련 법이 아니라는 뜻처럼 헷갈린다."""
    for keyword, category in keywords_map.items():
        if keyword in bill_name:
            return category
    return committee_abbr(committee) or "배정 안됨"


def _extract_rows(payload, service_id):
    """열린국회정보 Open API 공통 응답 포맷({서비스ID: [{head:...}, {row:[...]}]})에서
    row 리스트만 뽑아낸다. 문서화가 부실한 API라 방어적으로 구조를 훑는다."""
    try:
        blocks = payload.get(service_id)
        if blocks is None and len(payload) == 1:
            blocks = next(iter(payload.values()))
        if not isinstance(blocks, list):
            return []
        for block in blocks:
            if isinstance(block, dict) and "row" in block:
                return block["row"] or []
    except Exception:
        pass
    return []


def get_assembly_api_with_retry(url, params, timeout=15, retries=2, retry_wait=10):
    """국회 열린국회정보 API 호출을 감싸서, 접속 실패(타임아웃 등)면 짧게 대기 후
    재시도한다. 이 API가 통째로 잠깐 안 붙는 경우가 있는데, 그때 첫 페이지 호출부터
    실패하면 재시도 없이 바로 0건으로 끝나서 그날 법안 수집이 통째로 스킵됐었다."""
    last_exc = None
    for attempt in range(retries + 1):
        try:
            return requests.get(url, params=params, timeout=timeout)
        except requests.exceptions.RequestException as e:
            last_exc = e
            if attempt < retries:
                print(f"[국회 API 연결 실패 - {retry_wait}초 후 재시도 {attempt+1}/{retries}] {e}")
                time.sleep(retry_wait)
                continue
            raise
    raise last_exc


def fetch_all_rows(service_id, label, extra_params=None):
    """열린국회정보 목록 API를 페이지 단위로 끝까지 다 가져온다.

    (결과 목록, 끝까지 다 받았는지)를 돌려준다. 중간 페이지에서 예외로 멈추면 뒷부분
    법안이 빠진 채로 오는데, 이걸 "목록에서 빠졌다"로 기록하면 안 되기 때문이다.
    첫 페이지가 비어 있는 것도(키 문제·필수값 누락 등) 덜 받은 것으로 본다.
    """
    results = []
    p_index = 1
    while p_index <= 300:  # 안전장치: 무한 루프 방지 (최대 3만 건)
        params = {"Type": "json", "pIndex": p_index, "pSize": 100, **(extra_params or {})}
        if ASSEMBLY_API_KEY:
            params["KEY"] = ASSEMBLY_API_KEY
        try:
            res = get_assembly_api_with_retry(f"{BASE_URL}/{service_id}", params)
            rows = _extract_rows(res.json(), service_id)
            if not rows:
                if p_index == 1:
                    print(f"[{label} 전체조회 - 결과 없음] status={res.status_code} body={res.text[:300]}")
                    return results, False
                break
            results.extend(rows)
            if p_index % 20 == 0:
                print(f"[{label} 전체조회] {p_index}페이지까지 누적 {len(results)}건")
            if len(rows) < 100:
                break
            p_index += 1
            time.sleep(0.1)
        except Exception as e:
            print(f"[{label} 전체조회 예외] {p_index}페이지: {e}")
            return results, False
    return results, True


def fetch_all_pending_bills():
    """계류의안 API로 국회 전체 계류 법안을 다 가져온다 (BILL_NAME 필터 없음)."""
    return fetch_all_rows(PENDING_BILL_API, "계류의안")


def fetch_likms_bill_status(bill_id):
    """likms 상세 페이지가 쓰는 상세조회(findBillDetail.do)에서 재의요구 여부(reexamYn
    "Y"/"N")·정부이송일(govTransDt)·공포일(announceDt)이 든 data를 받는다. 열린국회정보
    API엔 재의요구 여부가 없다(probe_assembly_api.py로 확인, 2026-10-30). 실패하면 None."""
    try:
        res = requests.get(LIKMS_BILL_STATUS_URL, params={"billId": bill_id}, timeout=15)
        data = res.json().get("data")
        if isinstance(data, dict) and data.get("billId") == bill_id:
            return data
        print(f"[likms 상세조회 - 결과 없음] {bill_id}: {res.text[:200]}")
    except Exception as e:
        print(f"[likms 상세조회 예외] {bill_id}: {e}")
    return None


def load_vetoed_bill_nos():
    """config/bill_vetoed.csv(의안번호,법안명,상태)의 의안번호 집합. likms 상세조회가
    실패했을 때만 쓰는 예비 목록이다. 파일이 없으면 빈 집합."""
    if not os.path.exists(BILL_VETOED_PATH):
        return set()
    df = pd.read_csv(BILL_VETOED_PATH, dtype=str, encoding="utf-8-sig").fillna("")
    return {n.strip() for n in df["의안번호"] if n.strip()}


# 본회의 처리안건의 BILL_NM은 "…일부개정법률안(안상훈의원 등 11인)"처럼 제안자가
# 붙어 오므로, 처리의안에서 이름을 못 얻었을 때 끝의 제안자 괄호를 떼어 쓴다.
PROPOSER_SUFFIX_RE = re.compile(r"\((?:[^()]*의원(?: 등 \d+인)?|[^()]*위원장|정부)\)$")


def fetch_passed_bills():
    """본회의에서 가결된 22대 법률안을 계류의안과 같은 필드 모양으로 돌려준다.

    본회의 처리안건(의결일·정부이송일·공포일)을 기준으로, 처리의안에서 같은 의안ID의
    상임위·법사위 정보를 덧붙인다. (행 목록, 대안에 흡수된 원안 행 목록, 둘 다 끝까지
    받았는지)를 돌려준다. 흡수된 원안은 계류의안에서 빠져 법안 목록에 없으므로, 가결된
    대안의 "반영된 법안"을 잇는 데만 쓴다(find_alt_bill_links의 extra_children).
    """
    plenary_rows, plenary_ok = fetch_all_rows(PLENARY_BILL_API, "본회의 처리안건", {"AGE": CURRENT_ERA_NO})
    processed_rows, processed_ok = fetch_all_rows(PROCESSED_BILL_API, "처리의안", {"AGE": CURRENT_ERA_NO})
    processed = {r.get("BILL_ID"): r for r in processed_rows if r.get("BILL_ID")}
    pairs = []
    for p in plenary_rows:
        bill_id = p.get("BILL_ID")
        result = p.get("PROC_RESULT_CD") or ""
        if not bill_id or not p.get("RGS_PROC_DT") or "가결" not in result:
            continue
        pairs.append((p, processed.get(bill_id, {})))
    # 본회의 처리안건에는 공포까지 간 법률안이 일부 아예 없다(2024-12-10 예산부수
    # 세법 6건 등 9건, 국회 의안 목록과 대조해 확인 - 2026-10-30). 처리의안에는
    # 가결로 들어 있으므로 거기서 채운다. 다만 처리의안은 거부권(재의요구) 뒤 재표결에서
    # 폐기된 법안도 "원안가결" 그대로 두고, 이를 가를 필드가 없다(실측). 그래서 채울
    # 후보(매번 30여 건)만 likms 상세조회(fetch_likms_bill_status)로 재의요구 여부와
    # 정부이송일·공포일을 받는다. 조회가 안 되면 config/bill_vetoed.csv로 거른다.
    plenary_ids = {p.get("BILL_ID") for p in plenary_rows}
    vetoed = load_vetoed_bill_nos()
    promulgation_cutoff = (datetime.now(KST) - timedelta(days=30)).strftime("%Y-%m-%d")
    supplemented, reexam_skipped, lookup_failed = 0, 0, 0
    for bill_id, q in processed.items():
        if bill_id in plenary_ids or "가결" not in (q.get("PROC_RESULT_CD") or "") or not q.get("PROC_DT"):
            continue
        status = fetch_likms_bill_status(bill_id)
        time.sleep(0.2)
        if status is None:
            lookup_failed += 1
            if str(q.get("BILL_NO") or "") in vetoed:
                continue
        # 재의요구됐어도 공포일이 있으면 공포된 것이다 - 방송법 2200851은 reexamYn "Y"인데
        # 국회 의안 목록엔 "공포"로 나온다(2026-10-30).
        elif status.get("reexamYn") == "Y" and not status.get("announceDt"):
            reexam_skipped += 1
            continue
        pairs.append(({
            "RGS_PROC_DT": q["PROC_DT"], "PROC_RESULT_CD": q["PROC_RESULT_CD"],
            "CURR_TRANS_DT": (status or {}).get("govTransDt") or "",
            "ANNOUNCE_DT": (status or {}).get("announceDt") or "",
            "LOOKUP_FAILED": status is None,
        }, q))
        supplemented += 1
    passed = []
    for p, q in pairs:
        bill_id = p.get("BILL_ID") or q.get("BILL_ID")
        result = p.get("PROC_RESULT_CD") or ""
        proposer = q.get("PROPOSER") or p.get("PROPOSER") or ""
        rep = re.match(r"^(\S+?)의원", proposer)
        passed.append({
            "BILL_ID": bill_id,
            "BILL_NO": q.get("BILL_NO") or p.get("BILL_NO") or "",
            "BILL_NAME": q.get("BILL_NAME") or PROPOSER_SUFFIX_RE.sub("", p.get("BILL_NM") or "").strip(),
            "PROPOSER": proposer,
            # 처리의안·본회의 처리안건 둘 다 대표발의자 필드가 없어서 "OOO의원 등 N인"에서
            # 뽑는다. 위원장·정부 제안은 계류의안과 마찬가지로 비워 둔다.
            "RST_PROPOSER": rep.group(1) if rep else "",
            "PROPOSE_DT": q.get("PROPOSE_DT") or p.get("PROPOSE_DT") or "",
            "CURR_COMMITTEE": q.get("CURR_COMMITTEE") or p.get("COMMITTEE_NM") or "",
            "COMMITTEE_DT": q.get("COMMITTEE_DT") or p.get("COMMITTEE_SUBMIT_DT") or "",
            "CMT_PRESENT_DT": q.get("CMT_PRESENT_DT") or p.get("COMMITTEE_PRESENT_DT") or "",
            "CMT_PROC_DT": q.get("CMT_PROC_DT") or p.get("COMMITTEE_PROC_DT") or "",
            "CMT_PROC_RESULT_CD": q.get("CMT_PROC_RESULT_CD") or "",
            "LAW_PRESENT_DT": q.get("LAW_PRESENT_DT") or p.get("LAW_PRESENT_DT") or "",
            "LAW_PROC_DT": q.get("LAW_PROC_DT") or p.get("LAW_PROC_DT") or "",
            "LAW_PROC_RESULT_CD": q.get("LAW_PROC_RESULT_CD") or "",
            "LINK_URL": q.get("LINK_URL") or p.get("LINK_URL") or "",
            "RGS_PROC_DT": p.get("RGS_PROC_DT") or "",
            "RGS_RESULT": result,
            "CURR_TRANS_DT": p.get("CURR_TRANS_DT") or "",
            "ANNOUNCE_DT": p.get("ANNOUNCE_DT") or "",
            # likms 조회가 실패해 공포일을 모르는 채로 채운 법안은, 헌법상 이송 15일 안에
            # 공포하거나 재의요구해야 하므로 의결 30일이 지났으면 공포된 것으로 본다
            # (derive_stage_and_result).
            "ASSUME_PROMULGATED": bool(p.get("LOOKUP_FAILED")) and p["RGS_PROC_DT"] <= promulgation_cutoff,
        })
    absorbed = [q for q in processed_rows
                if "대안반영" in (q.get("CMT_PROC_RESULT_CD") or "") + (q.get("PROC_RESULT_CD") or "")]
    print(f"[본회의 가결 법률안] {len(passed)}건 (본회의 처리안건 {len(plenary_rows)}건, 처리의안 {len(processed_rows)}건 중,"
          f" 처리의안에서 채운 {supplemented}건 포함, 재의요구라 뺀 {reexam_skipped}건,"
          f" likms 조회 실패 {lookup_failed}건 - 실패분은 제외 목록 {len(vetoed)}건으로 거름)"
          f" / 대안에 흡수된 원안 {len(absorbed)}건")
    return passed, absorbed, plenary_ok and processed_ok


PARTY_ABBR = {
    "더불어민주당": "민주당",
    "새정치민주연합": "새정치연합",
    "미래통합당": "통합당",
    "자유한국당": "한국당",
    "새누리당": "새누리당",
}


def party_abbr(name):
    if not name:
        return name
    return PARTY_ABBR.get(name, name)


# ALLNAMEMBER의 정당 칸(PLPT_NM)은 "당선 당시" 정당이라, 위성정당 비례대표로
# 당선된 사람은 위성정당이 해산되고 원래 당(사회민주당·진보당·기본소득당 등)으로
# 돌아간 뒤에도 계속 "더불어민주연합"/"국민의미래"로 남는다(실측: 한창민 -
# ALLNAMEMBER엔 더불어민주연합, 현직 API엔 사회민주당). 현재 소속은 현직 의원
# 인적사항 API(POLY_NM)가 정확하므로 그걸 우선하고, 위성정당은 "이전 정당"
# 이력에서도 뺀다(선거용으로 잠깐 적만 둔 곳이라 "前 더불어민주연합"은 오해를 준다).
CURRENT_MEMBER_API = "nwvrqwxyaytdsfvhu"  # 국회의원 인적사항(현직) - POLY_NM이 현재 소속정당
SATELLITE_PARTIES = {"더불어민주연합", "국민의미래", "더불어시민당", "미래한국당"}


def fetch_current_parties():
    """현직 의원 인적사항 API에서 {의원코드(MONA_CD): 현재 소속정당} 사전을 만든다.
    실패하면 빈 사전 - 그때는 ALLNAMEMBER 정당을 그대로 쓴다."""
    parties = {}
    p_index = 1
    while p_index <= 10:  # 안전장치: 현직은 300명 남짓
        params = {"KEY": ASSEMBLY_API_KEY, "Type": "json", "pIndex": p_index, "pSize": 100}
        try:
            res = get_assembly_api_with_retry(f"{BASE_URL}/{CURRENT_MEMBER_API}", params, timeout=40)
            if res.status_code != 200:
                print(f"[현직 의원 정당 조회 실패] {p_index}페이지: status={res.status_code} body={res.text[:300]}")
                break
            rows = _extract_rows(res.json(), CURRENT_MEMBER_API)
            if not rows:
                break
            for row in rows:
                code = row.get("MONA_CD")
                party = (row.get("POLY_NM") or "").strip()
                if code and party:
                    parties[code] = party
            if len(rows) < 100:
                break
            p_index += 1
            time.sleep(0.1)
        except Exception:
            print(f"[현직 의원 정당 조회 예외] {p_index}페이지:\n{traceback.format_exc()}")
            break
    print(f"[현직 의원 정당 조회] {len(parties)}명 확인")
    return parties


def resolve_parties(plpt_nm, current_party=""):
    """(현재 정당, 이전 정당)을 정식명으로 돌려준다. current_party가 있으면 그걸
    현재 정당으로 쓰고, 그때는 이력에서 위성정당을 뺀다. 없으면 예전처럼
    PLPT_NM의 마지막/그 앞 조각을 쓴다."""
    history = [p.strip() for p in (plpt_nm or "").split("/") if p.strip()]
    if current_party:
        history = [p for p in history if p not in SATELLITE_PARTIES]
        if not history or history[-1] != current_party:
            history.append(current_party)
    return (history[-1] if history else "", history[-2] if len(history) >= 2 else "")


def fetch_member_info():
    """국회의원 인적사항 API에서 이름 -> {정당, 이전정당, 지역구, 선수} 사전을 만든다.
    한 사람당 한 행에 그동안 거쳐온 소속정당이 '/'로 이어져 있어서(예: "더불어민주당/조국혁신당"),
    마지막 조각을 현재 정당으로, 그 바로 앞 조각을 이전 정당으로 쓴다.

    (사전, 22대 현역 원본행 목록)을 돌려준다. 원본행은 의원 검색 화면(member_list.csv)에
    쓰는데, 같은 API를 두 번 훑지 않으려고 여기서 같이 걷어 온다."""
    current_parties = fetch_current_parties()
    info = {}
    current_rows = []
    era_samples = []  # 22대를 하나도 못 찾았을 때, GTELT_ERACO가 기대와 다른 형태인지 보려고 남긴다
    ambiguous_current_names = set()  # 22대 안에서도 동명이인인 이름(예: 박지원) - 아래서 info에서 제거함
    p_index = 1
    while p_index <= 50:  # 안전장치: 전체 역대 의원 약 3300명 수준이면 충분
        params = {"Type": "json", "pIndex": p_index, "pSize": 100}
        if ASSEMBLY_API_KEY:
            params["KEY"] = ASSEMBLY_API_KEY
        try:
            # 각 행에 약력(BRF_HST) 같은 긴 텍스트가 붙어 있어 계류의안 API보다
            # 페이지당 응답이 무겁다. 15초로는 첫 페이지부터 타임아웃이 나서
            # 전체가 빈 채로 끝난 적이 있어(2026-08-28) 넉넉히 준다.
            res = get_assembly_api_with_retry(f"{BASE_URL}/{MEMBER_INFO_API}", params, timeout=40)
            if res.status_code != 200:
                print(f"[국회의원 인적사항 조회 실패] {p_index}페이지: status={res.status_code} body={res.text[:300]}")
                break
            rows = _extract_rows(res.json(), MEMBER_INFO_API)
            if not rows:
                if p_index == 1:
                    print(f"[국회의원 인적사항 조회 - 결과 없음] status={res.status_code} body={res.text[:300]}")
                break
            for row in rows:
                name = row.get("NAAS_NM")
                if not name:
                    continue
                era = row.get("GTELT_ERACO") or ""
                is_current = CURRENT_ERA in era
                # 동명이인 안전장치 ①: 역대(전체 회기) 데이터를 이름만으로 겹쳐 쓰다 보니
                # 22대 현직 의원과 이름이 같은 과거 의원이 있으면(실측: 조정훈 -
                # 현직은 국민의힘·서울 마포구갑인데 민주당(前무소속)·전북 남원군갑인
                # 동명이인 옛 의원 데이터로 덮어써짐), 나중 페이지에서 처리되는 과거
                # 데이터가 현직 데이터를 지워버리는 사고가 났다. 한 번 현직 데이터가
                # 들어간 이름은 과거 데이터로 다시 덮어쓰지 않는다(현직 데이터가 항상 우선).
                #
                # 동명이인 안전장치 ②: 22대 안에서도 동명이인이 실제로 있다(실측:
                # "박지원" 의원이 전북/전남 지역구로 2명). 법안 API는 대표발의자를
                # 이름만 주고 의원코드를 안 줘서 어느 쪽인지 코드로 구분할 방법이
                # 없다 - 이런 이름은 아예 info에서 빼서, 틀릴 수 있는 정당/지역구를
                # 억지로 보여주는 대신 이름만 그대로 두게 한다(format_proposer의
                # "info 없으면 원문 유지" 폴백을 그대로 활용).
                if is_current and name in info and info[name].get("_current"):
                    ambiguous_current_names.add(name)
                elif name in info and not is_current and info[name].get("_current"):
                    pass
                else:
                    party, prev_party = resolve_parties(
                        row.get("PLPT_NM"), current_parties.get(row.get("NAAS_CD")) if is_current else "")
                    info[name] = {
                        "party": party,
                        "prev_party": prev_party,
                        # 지역구가 비어있으면(API가 안 채워준 경우도 포함) 지역구 없는
                        # 의원은 곧 비례대표라는 뜻이라 "비례대표"라고 명시한다.
                        "district": (row.get("ELECD_NM") or "").split("/")[-1].strip() or "비례대표",
                        "term": row.get("RLCT_DIV_NM") or "",
                        "_current": is_current,
                    }
                if is_current:
                    # save_members에서도 같은 현재 정당을 쓰도록 행에 붙여 둔다.
                    row["_CURRENT_PARTY"] = current_parties.get(row.get("NAAS_CD"), "")
                    current_rows.append(row)
                elif len(era_samples) < 5:
                    era_samples.append(era)
            if len(rows) < 100:
                break
            p_index += 1
            time.sleep(0.1)
        except Exception:
            # 예전엔 여기서 str(e)만 남겨서(예: "'NoneType' object has no attribute
            # 'get'") 실제로 어디가 문제였는지 다음 실행에서 원인을 못 짚었다.
            # traceback을 그대로 남겨서 다음 실패 때는 바로 보이게 한다.
            print(f"[국회의원 인적사항 조회 예외] {p_index}페이지:\n{traceback.format_exc()}")
            break
    print(f"[국회의원 인적사항 조회] {p_index}페이지까지 훑음, 역대 {len(info)}명 / {CURRENT_ERA} 현역 {len(current_rows)}명")
    if info and not current_rows:
        print(f"[국회의원 인적사항 조회 - 22대 0명] GTELT_ERACO 값 예시: {era_samples}")
    if ambiguous_current_names:
        for n in ambiguous_current_names:
            info.pop(n, None)
        print(f"[국회의원 인적사항 조회] 22대 안에서도 동명이인 있어 정당/지역구 표시 생략: {', '.join(sorted(ambiguous_current_names))}")

    changed = [row.get("NAAS_NM") for row in current_rows if row.get("NAAS_NM") in MEMBER_ROLE_CHANGES]
    if changed:
        print(f"[의원 명단] 다른 자리로 옮긴 의원(명단엔 유지, 現 직함만 표시): {', '.join(changed)}")
    lost = [row.get("NAAS_NM") for row in current_rows if row.get("NAAS_NM") in MEMBER_SEAT_LOST]
    if lost:
        print(f"[의원 명단] 확정판결로 의원직 상실(명단엔 유지, 별도 배지 표시): {', '.join(lost)}")
    return info, current_rows


def fetch_committee_chairs():
    """위원회 위원 명단 API에서 JOB_RES_NM(구성)이 '위원장'인 행만 걸러
    {의원코드(MONA_CD): {위원회명, ...}} 사전을 만든다.

    예전엔 약력(BRF_HST) 텍스트에서 "現 OO위원회 위원장" 패턴을 정규식으로
    찾았는데, 약력 표기가 사람마다 달라서("現)", "(현)", "위원장(현)", 現/현
    자체가 없는 경우까지) 실제 위원장 다수를 놓쳤다. 이 API는 JOB_RES_NM
    필드로 위원장/간사/위원을 명확히 구분해 주므로 훨씬 신뢰할 수 있다."""
    chairs = {}
    p_index = 1
    while p_index <= 20:  # 안전장치: 상임위+특별위 다 합쳐도 위원장은 20명 남짓
        params = {"KEY": ASSEMBLY_API_KEY, "Type": "json", "pIndex": p_index, "pSize": 100, "JOB_RES_NM": "위원장"}
        try:
            res = get_assembly_api_with_retry(f"{BASE_URL}/{COMMITTEE_MEMBER_API}", params, timeout=15)
            if res.status_code != 200:
                print(f"[위원회 위원장 조회 실패] {p_index}페이지: status={res.status_code} body={res.text[:300]}")
                break
            rows = _extract_rows(res.json(), COMMITTEE_MEMBER_API)
            if not rows:
                break
            for row in rows:
                code = row.get("MONA_CD")
                dept = row.get("DEPT_NM")
                if not code or not dept:
                    continue
                chairs.setdefault(code, set()).add(dept)
            if len(rows) < 100:
                break
            p_index += 1
            time.sleep(0.1)
        except Exception:
            print(f"[위원회 위원장 조회 예외] {p_index}페이지:\n{traceback.format_exc()}")
            break
    print(f"[위원회 위원장 조회] {len(chairs)}명 확인")
    return chairs


MEMBER_COLUMNS = [
    "의원코드", "이름", "한자명", "정당", "이전정당", "선수", "당선대수",
    "지역구", "선거구구분", "소속위원회", "위원장위원회", "성별", "생년월일", "전화", "이메일",
    "홈페이지", "사무실", "보좌관", "비서관", "비서", "약력", "사진",
    "영문명", "현직변경", "의원직상실",
]


def save_members(rows, chairs=None):
    """22대 현역 의원을 member_list.csv 로 저장한다 (의원 검색 화면용).

    정당/선거구/위원회 칸은 거쳐온 이력이 '/'로 이어져 있어서 마지막 조각이 현재값이다.
    위원회는 한 사람이 여러 곳에 속하므로 쉼표로 이어진 문자열을 그대로 둔다."""
    if not rows:
        # API가 그 순간 잠깐 안 붙어서 못 받은 것뿐일 수 있으므로, 이미 어제 받아둔
        # 파일이 있으면 빈 값으로 덮어써서 명단을 날리지 않는다. 파일이 아예
        # 없을 때만(맨 처음 실행) 헤더만 있는 빈 CSV를 만든다 - git add가
        # "그런 파일 없음"으로 실패하지 않게 하기 위한 최소한의 존재 보장이다.
        if os.path.exists(MEMBER_LIST_PATH):
            print("[의원 명단] 22대 현역을 못 찾아 기존 member_list.csv를 그대로 둡니다.")
        else:
            pd.DataFrame(columns=MEMBER_COLUMNS).to_csv(MEMBER_LIST_PATH, index=False, encoding="utf-8-sig")
            print("[의원 명단] 22대 현역을 못 찾아 빈 member_list.csv를 만듭니다.")
        return

    def last(value):
        return (value or "").split("/")[-1].strip()

    chairs = chairs or {}
    members = []
    for row in rows:
        party, prev_party = resolve_parties(row.get("PLPT_NM"), row.get("_CURRENT_PARTY", ""))
        code = row.get("NAAS_CD", "")
        members.append({
            "의원코드": code,
            "이름": row.get("NAAS_NM", ""),
            "한자명": row.get("NAAS_CH_NM", "") or "",
            # 법안 카드의 제안자 문구도 약칭("민주당")을 쓰므로 여기서도 맞춘다.
            # 정식명 그대로 두면 같은 정당이 화면마다 다르게 보인다.
            "정당": party_abbr(party),
            "이전정당": party_abbr(prev_party),
            "선수": row.get("RLCT_DIV_NM", "") or "",
            "당선대수": row.get("GTELT_ERACO", "") or "",
            "지역구": last(row.get("ELECD_NM")) or "비례대표",
            "선거구구분": last(row.get("ELECD_DIV_NM")) or "",
            "소속위원회": last(row.get("CMIT_NM")) or "",
            "위원장위원회": ", ".join(sorted(chairs.get(code, ()))),
            "성별": row.get("NTR_DIV", "") or "",
            "생년월일": row.get("BIRDY_DT", "") or "",
            "전화": row.get("NAAS_TEL_NO", "") or "",
            "이메일": row.get("NAAS_EMAIL_ADDR", "") or "",
            "홈페이지": row.get("NAAS_HP_URL", "") or "",
            "사무실": row.get("OFFM_RNUM_NO", "") or "",
            "보좌관": row.get("AIDE_NM", "") or "",
            "비서관": row.get("CHF_SCRT_NM", "") or "",
            "비서": row.get("SCRT_NM", "") or "",
            "약력": row.get("BRF_HST", "") or "",
            "사진": row.get("NAAS_PIC", "") or "",
            # 의원 홈페이지 프로필 주소는 영문명에서 공백만 뺀 문자열이다
            # (실제로 확인함: "KANG KYUNGSOOK" -> .../22nd/KANGKYUNGSOOK).
            # 영문명이 없는 사람은 이 칸도 비어서 화면에서 링크를 안 만든다.
            "영문명": (row.get("NAAS_EN_NM") or "").replace(" ", ""),
            "현직변경": MEMBER_ROLE_CHANGES.get(row.get("NAAS_NM", ""), ""),
            "의원직상실": MEMBER_SEAT_LOST.get(row.get("NAAS_NM", ""), ""),
        })
    members.sort(key=lambda m: m["이름"])
    pd.DataFrame(members).to_csv(MEMBER_LIST_PATH, index=False, encoding="utf-8-sig")
    print(f"[의원 명단 저장] {CURRENT_ERA} 현역 {len(members)}명 -> {MEMBER_LIST_PATH}")


def format_proposer(raw_proposer, rep_name, member_info):
    """제안자 표시 문구("OOO의원 등 N인")의 대표발의자 이름 뒤에 정당/선수/지역구를
    괄호로 덧붙인다. 당적을 옮긴 적이 있으면 현재 정당 뒤에 "(前 이전정당)"을 붙인다.
    인적사항을 못 찾으면 원래 문구를 그대로 둔다."""
    if not rep_name:
        return raw_proposer
    info = member_info.get(rep_name)
    if not info:
        return raw_proposer
    party = party_abbr(info["party"])
    if info["prev_party"] and info["prev_party"] != info["party"]:
        party = f"{party}(前 {party_abbr(info['prev_party'])})"
    detail = ", ".join(v for v in [party, info["term"], info["district"]] if v)
    if not detail:
        return raw_proposer
    m = re.search(r"(\s*등\s*\d+인)\s*$", raw_proposer or "")
    suffix = m.group(1) if m else ""
    return f"{rep_name}의원({detail}){suffix}"


def normalize_result(code):
    """세부 표결/처리 결과를 큰 범주로 묶는다 - 원안가결/수정가결/대안반영가결처럼
    비슷한 결과끼리 칩이 너무 잘게 쪼개지지 않도록 함."""
    if not code:
        return code
    if "부결" in code:
        return "부결"
    if "가결" in code:
        return "가결"
    if "철회" in code or "폐기" in code:
        return "폐기"
    return code


# 열린국회정보 Open API에는 "원안이 어느 대안에 흡수됐는지" 알려주는 필드가
# 없다(실측 확인, 2026-09-22 - "법률안 심사 및 처리(위원회안,대안)"·"의안검색"
# API 응답을 직접 떠서 BILL_ID_REF 같은 연결 필드가 실제로는 비어있음을 확인함).
# 대신 같은 소관위 + 같은 상임위처리일 + 법안명이 "(대안)" 접미사를 뺀 원안명과
# 정확히 일치하면 사실상 유일하게 특정된다는 걸 실측으로 확인했다(같은 날 같은
# 위원회에서 나온 여러 대안 중 이름이 겹치는 경우가 없었음) - 그 방식으로 간접
# 매칭한다. 여러 법을 하나로 묶어 완전히 다른 이름을 붙이는 경우는 못 잡지만,
# 이름이 그대로 이어지는(가장 흔한) 경우는 잡힌다.
ALT_BILL_SUFFIX = "(대안)"


def find_alt_bill_links(bills, extra_children=()):
    """bills(딕트 리스트, in-place)에서 대안반영가결/폐기된 원안에는
    대안의안ID/대안법안명/대안상세링크를, 그 대안 자신에는 반영된원안목록
    (JSON 문자열)을 채운다. extra_children은 목록에 없는 원안(같은 키 모양의
    딕트) - 대안의 반영된원안목록에만 더해진다."""
    alt_index = {}
    for b in bills:
        name = b["법안명"]
        if name.endswith(ALT_BILL_SUFFIX):
            core = name[:-len(ALT_BILL_SUFFIX)].strip()
            alt_index[(b["소관위원회"], b["상임위처리일"], core)] = b

    alt_children = {}
    listed = {b["의안ID"] for b in bills}
    extra = [c for c in extra_children if c["의안ID"] not in listed]
    for b in [*bills, *extra]:
        if "대안반영" not in b["처리결과"]:
            continue
        alt = alt_index.get((b["소관위원회"], b["상임위처리일"], b["법안명"]))
        if not alt or alt["의안ID"] == b["의안ID"]:
            continue
        if b["의안ID"] in listed:
            b["대안의안ID"] = alt["의안ID"]
            b["대안법안명"] = alt["법안명"]
            b["대안상세링크"] = alt["상세링크"]
        alt_children.setdefault(alt["의안ID"], []).append({
            "의안ID": b["의안ID"], "법안명": b["법안명"],
            "제안자": b["제안자"], "상세링크": b["상세링크"],
        })

    for b in bills:
        children = alt_children.get(b["의안ID"])
        if children:
            b["반영된원안목록"] = json.dumps(children, ensure_ascii=False)


# 법사위 결과가 이 값이면 법사위에서 끝나는 게 아니라 본회의 표결 대기로 넘어간 것이다.
# (대안반영가결/대안반영폐기는 원래 법안 대신 별도 대안이 가결되는 것이라 이 법안
# 자체는 법사위에서 끝난다 - 그래서 포함하지 않음)
LAW_FORWARD_RESULTS = {"원안가결", "수정가결"}


def derive_stage_and_result(row):
    """계류의안 응답 자체에 담긴 심사 단계 필드를 보고, 법안이 지금 실제로 대기 중인
    단계(처리단계, 원본 결과코드)를 반환한다
    (입안 및 발의 -> 상임위 심사 -> 법사위 심사 -> 본회의 의결).
    상임위가 가결시켜 법사위로 회부된 법안은 LAW_PRESENT_DT가 잡히면서 이미
    "법사위 심사"로 넘어간다(상임위 결과는 더 이상 안 보여줌 - 지금 실제 위치가
    아니므로). 마찬가지로 법사위가 가결시킨(원안가결/수정가결) 법안은 본회의
    표결을 기다리는 중이니 "본회의 의결"로 넘긴다 - 법사위 결과 필드값으로
    간접 판단하는 것(본회의 회부를 직접 알려주는 날짜 필드는 이 API에 없음).
    법사위가 부결/철회/폐기/대안반영으로 끝낸 건 그 자리가 최종 단계라 그대로
    "법사위 심사"에 남는다. 본회의 의결 이후(정부 이송/공포)는 이 API 응답에
    아예 없음 - "계류"(아직 안 끝난) 법안만 주는 API라 그 단계까지 간 법안은
    애초에 여기 나오지 않기 때문.

    본회의 처리안건에서 더한 가결 법률안(fetch_passed_bills, RGS_PROC_DT가 있음)은
    "공포" 단계로 두고, 공포일이 찍혔으면 결과 "공포", 아직이면 "정부이송"이다."""
    if row.get("RGS_PROC_DT"):
        return "공포", "공포" if row.get("ANNOUNCE_DT") or row.get("ASSUME_PROMULGATED") else "정부이송"
    if row.get("LAW_PROC_RESULT_CD"):
        result = row["LAW_PROC_RESULT_CD"]
        if result in LAW_FORWARD_RESULTS:
            return "본회의 의결", ""
        return "법사위 심사", result
    if row.get("LAW_PRESENT_DT"):
        return "법사위 심사", ""
    if row.get("CMT_PROC_RESULT_CD"):
        return "상임위 심사", row["CMT_PROC_RESULT_CD"]
    if row.get("CMT_PROC_DT") or row.get("COMMITTEE_DT") or row.get("CMT_PRESENT_DT"):
        return "상임위 심사", ""
    return "입안 및 발의", ""


def load_previous_bills():
    """직전 실행 결과(bill_list.csv)를 {의안ID: 행(dict)}로 읽어온다.

    상태변경 감지에는 처리상태만 쓰지만, 이번에 목록에서 빠진 법안은 이름·번호를
    이번 응답에서 얻을 수 없으므로 변동 로그용으로 행 전체를 들고 있는다.
    """
    if not os.path.exists(BILL_LIST_PATH):
        return {}
    try:
        df = pd.read_csv(BILL_LIST_PATH, dtype=str, keep_default_na=False)
        return {r["의안ID"]: r for r in df.to_dict("records")}
    except Exception as e:
        print(f"[이전 bill_list.csv 로드 실패] {e}")
        return {}


def append_status_log(bills, prev_bills, fetch_complete, now_str):
    """이번 실행에서 감지한 신규 발의/상태변경/목록 제외를 bill_status_log.csv에 덧붙인다.

    직전 bill_list.csv가 없으면(첫 실행) 전부 "신규"로 찍히므로 아무것도 안 남긴다.
    """
    if not prev_bills:
        return
    # 본회의 가결 법률안("공포" 단계)을 처음 받아오는 실행에는 22대 가결 법안 전부가
    # 한꺼번에 "신규"로 잡히므로, 직전 목록에 공포 단계가 하나도 없으면 그건 안 남긴다.
    passed_backfill = not any(r.get("처리단계") == "공포" for r in prev_bills.values())
    entries = []
    for b in bills:
        prev = prev_bills.get(b["의안ID"])
        if prev is None:
            if passed_backfill and b["처리단계"] == "공포":
                continue
            kind, before = "신규", ""
        elif b["상태변경"]:
            kind, before = "상태변경", prev.get("처리상태", "")
        else:
            continue
        entries.append({
            "감지일시": now_str, "구분": kind, "의안ID": b["의안ID"], "의안번호": b["의안번호"],
            "법안명": b["법안명"], "소관위원회": b["소관위원회"], "이전상태": before, "새상태": b["처리상태"],
        })
    if fetch_complete:
        current_ids = {b["의안ID"] for b in bills}
        for bill_id, prev in prev_bills.items():
            if bill_id in current_ids:
                continue
            # 계류의안 API는 본회의 의결 이후를 안 주므로 "왜" 빠졌는지(가결/부결/철회/
            # 폐기)는 여기서 알 수 없다 - 새상태는 비워 둔다.
            entries.append({
                "감지일시": now_str, "구분": "목록제외", "의안ID": bill_id, "의안번호": prev.get("의안번호", ""),
                "법안명": prev.get("법안명", ""), "소관위원회": prev.get("소관위원회", ""),
                "이전상태": prev.get("처리상태", ""), "새상태": "",
            })
    else:
        print("[변동 로그] 계류의안 조회가 중간에 끊겨 이번엔 목록 제외를 기록하지 않습니다.")
    if not entries:
        return
    write_header = not os.path.exists(BILL_STATUS_LOG_PATH)
    pd.DataFrame(entries, columns=BILL_STATUS_LOG_COLUMNS).to_csv(
        BILL_STATUS_LOG_PATH, mode="a", header=write_header, index=False,
        encoding="utf-8-sig" if write_header else "utf-8",
    )
    counts = Counter(e["구분"] for e in entries)
    print(f"[변동 로그] {', '.join(f'{k} {v}건' for k, v in counts.items())} -> {BILL_STATUS_LOG_PATH}")


def load_previous_summaries():
    """직전 실행 결과에서 {의안ID: AI요약}을 읽어온다 (제안이유는 안 바뀌므로 재사용)."""
    if not os.path.exists(BILL_LIST_PATH):
        return {}
    try:
        df = pd.read_csv(BILL_LIST_PATH)
        if "AI요약" not in df.columns:
            return {}
        return {
            bid: s for bid, s in zip(df["의안ID"], df["AI요약"])
            if isinstance(s, str) and s.strip()
        }
    except Exception as e:
        print(f"[이전 bill_list.csv 요약 로드 실패] {e}")
        return {}


def fetch_bill_proposal_text(bill_id):
    """billSummary.do 팝업 페이지에서 '제안이유 및 주요내용' 뒤 텍스트를 긁어온다.
    정확한 HTML 구조를 문서로 확인할 수 없어서, 태그 대신 페이지 전체 텍스트에서
    표제 문구를 찾아 그 뒤를 잘라내는 방식으로 - 구조가 조금 달라져도 잘 버틴다."""
    try:
        res = requests.get(BILL_SUMMARY_URL, params={"billId": bill_id}, timeout=15)
        soup = BeautifulSoup(res.text, "html.parser")
        full_text = soup.get_text("\n", strip=True)
        for marker in ["제안이유 및 주요내용", "제안이유"]:
            idx = full_text.find(marker)
            if idx != -1:
                remainder = full_text[idx + len(marker):].strip()
                return re.sub(r"\n{2,}", "\n", remainder)[:1500]
    except Exception as e:
        print(f"[제안이유 스크래핑 예외] {bill_id}: {e}")
    return ""


def summarize_bills_with_gemini(items):
    """[{"idx", "name", "text"}] 목록을 배치로 Gemini에 보내 한 줄 요약을 받는다."""
    if not GEMINI_API_KEY or not items:
        return {}
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent"
    results = {}
    consecutive_failures = 0
    batches = [items[i:i + 20] for i in range(0, len(items), 20)]
    for batch in batches:
        # 서킷 브레이커: Gemini가 지금 불안정해서 재시도까지 연달아 실패하는 상황이면,
        # 남은 배치까지 전부 붙잡고 있지 않고 여기서 포기한다. 못 끝낸 법안은 요약이
        # 빈 채로 저장되고, 다음 실행 때 다시 시도된다 (제안이유는 안 바뀌므로 손해 없음).
        if consecutive_failures >= 3:
            print(f"[법안 요약] 연속 {consecutive_failures}회 실패 - Gemini 불안정으로 판단해 남은 배치는 다음 실행으로 미룸")
            break
        input_data = [{"idx": it["idx"], "name": it["name"], "text": it["text"]} for it in batch]
        prompt = f"""당신은 법안을 쉬운 말로 요약하는 담당자입니다.
입력 법안 목록: {json.dumps(input_data, ensure_ascii=False)}

각 법안의 제안이유/주요내용(text)을 읽고, 일반인이 이해하기 쉬운 한국어 1문장으로 요약하세요.
법률 용어를 그대로 나열하지 말고 "무엇을 왜 바꾸려는지"가 드러나게 쓰세요.
문장 종결은 "~합니다/~하려고 합니다" 같은 정중체 대신 "~함/~하도록 함"처럼 간결한
개조식으로 끝내세요. 예(좋음): "유휴부지를 재생에너지 시설로 활용할 수 있도록 허가함".
예(나쁨): "유휴부지를 재생에너지 시설로 활용할 수 있도록 허가하려고 합니다".
text가 비어있거나 의미를 알 수 없으면 summary를 빈 문자열로 두세요.

응답은 입력 개수만큼, 각 항목에 idx를 포함해 아래 형식의 JSON 배열로만 출력하세요:
[
  {{"idx": 0, "summary": "..."}}
]
"""
        payload = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"response_mime_type": "application/json", "thinkingConfig": {"thinkingLevel": "low"}}}
        try:
            res = post_gemini_with_retry(url, payload)
            if res.status_code == 200:
                consecutive_failures = 0
                res_json = res.json()
                raw_text = res_json['candidates'][0]['content']['parts'][0]['text'].strip()
                if raw_text.startswith("```json"): raw_text = raw_text[7:]
                if raw_text.startswith("```"): raw_text = raw_text[3:]
                if raw_text.endswith("```"): raw_text = raw_text[:-3]
                for item in json.loads(raw_text.strip()):
                    if not isinstance(item, dict):
                        continue
                    i = item.get("idx")
                    if i is not None:
                        results[i] = (item.get("summary") or "").strip()
            else:
                consecutive_failures += 1
                print(f"[법안 요약 오류] status={res.status_code} body={res.text[:300]}")
        except Exception as e:
            consecutive_failures += 1
            print(f"[법안 요약 예외] {e}")
        time.sleep(4.5)  # 무료 등급은 분당 15회 제한
    return results


def main():
    if not ASSEMBLY_API_KEY:
        print("[경고] ASSEMBLY_API_KEY가 없습니다 - 키 없이 호출을 시도합니다 (제한/실패 가능).")

    keywords_map = load_bill_keywords()
    rows, fetch_complete = fetch_all_pending_bills()
    print(f"[계류의안 전체조회 완료] 총 {len(rows)}건")

    member_info, current_members = fetch_member_info()
    print(f"[국회의원 인적사항 조회 완료] 역대 {len(member_info)}명 / {CURRENT_ERA} 현역 {len(current_members)}명")
    # 22대 안에서도 동명이인인 이름(예: 박지원 2명) - 법안 화면에서 "!" 표시로
    # "정당/지역구를 확신할 수 없다"는 걸 알려주기 위해 따로 챙겨둔다.
    name_counts = Counter(r.get("NAAS_NM") for r in current_members)
    ambiguous_names = {n for n, c in name_counts.items() if n and c > 1}
    if ambiguous_names:
        print(f"[국회의원 인적사항 조회] 22대 안에서도 동명이인: {', '.join(sorted(ambiguous_names))}")
    chairs = fetch_committee_chairs()
    save_members(current_members, chairs)

    seen = {}
    for row in rows:
        bill_id = row.get("BILL_ID")
        if not bill_id or bill_id in seen:
            continue
        seen[bill_id] = row

    # 본회의를 통과한 법안은 계류의안에서 빠지므로 따로 받아 "공포" 단계로 더한다.
    # 혹시 계류의안에도 남아 있으면 본회의 쪽이 더 뒤 단계라 그걸로 덮는다.
    passed_rows, absorbed_rows, passed_complete = fetch_passed_bills()
    for row in passed_rows:
        seen[row["BILL_ID"]] = row
    fetch_complete = fetch_complete and passed_complete

    prev_bills = load_previous_bills()
    prev_status = {bid: r.get("처리상태", "") for bid, r in prev_bills.items()}

    # 안전장치: API 장애/키 문제/일시적 오류 등으로 이번에 새로 가져온 건수가 기존 대비
    # 크게 줄었으면, 그 결과로 기존 bill_list.csv를 덮어쓰지 않고 그대로 둔다.
    if prev_status and len(seen) < len(prev_status) * 0.3:
        print(f"[경고] 이번 수집 결과({len(seen)}건)가 기존({len(prev_status)}건)보다 크게 적습니다. "
              f"API 오류로 의심되어 bill_list.csv를 덮어쓰지 않고 종료합니다.")
        return

    prev_summaries = load_previous_summaries()
    now_kst = datetime.now(KST)
    now_str = now_kst.strftime("%Y-%m-%d %H:%M")
    summary_cutoff = (now_kst - timedelta(days=RECENT_DAYS_FOR_SUMMARY)).strftime("%Y-%m-%d")

    bills = []
    for bill_id, row in seen.items():
        stage, raw_result = derive_stage_and_result(row)
        status = f"{stage} · {normalize_result(raw_result)}" if raw_result else stage
        is_new = bill_id not in prev_status
        # 그냥 "변경됐다"만 표시하지 않고, 실제로 어디서 어디로 넘어갔는지 바로 보이게
        # "이전상태 -> 새상태" 문구를 통째로 저장한다.
        changed = "" if is_new or prev_status[bill_id] == status else f"{prev_status[bill_id]} → {status}"
        bills.append({
            "의안ID": bill_id,
            "의안번호": row.get("BILL_NO", ""),
            "법안명": row.get("BILL_NAME", ""),
            "카테고리": categorize_bill(row.get("BILL_NAME", ""), keywords_map, row.get("CURR_COMMITTEE", "")),
            "제안자": format_proposer(row.get("PROPOSER", ""), row.get("RST_PROPOSER", ""), member_info),
            "대표발의자": row.get("RST_PROPOSER", ""),
            "대표발의자동명이인": row.get("RST_PROPOSER", "") in ambiguous_names,
            "제안일": row.get("PROPOSE_DT", ""),
            "소관위원회": row.get("CURR_COMMITTEE", ""),
            "처리상태": status,
            "처리단계": stage,
            "처리결과": raw_result if raw_result else "심사중",
            "상임위회부일": row.get("COMMITTEE_DT") or row.get("CMT_PRESENT_DT") or "",
            "상임위처리일": row.get("CMT_PROC_DT", ""),
            "상임위결과": row.get("CMT_PROC_RESULT_CD", ""),
            "법사위회부일": row.get("LAW_PRESENT_DT", ""),
            "법사위처리일": row.get("LAW_PROC_DT", ""),
            "법사위결과": row.get("LAW_PROC_RESULT_CD", ""),
            "본회의의결일": row.get("RGS_PROC_DT", ""),
            "본회의결과": row.get("RGS_RESULT", ""),
            "정부이송일": row.get("CURR_TRANS_DT", ""),
            "공포일": row.get("ANNOUNCE_DT", ""),
            "상태변경": changed,
            "상세링크": row.get("LINK_URL", ""),
            "AI요약": prev_summaries.get(bill_id, ""),
            "최종수집일": now_str,
            # 대안반영가결/폐기 원안 -> 이를 흡수한 대안 (find_alt_bill_links가 채움)
            "대안의안ID": "",
            "대안법안명": "",
            "대안상세링크": "",
            # 위원회 대안 자신 -> 이 대안에 반영된 원안 목록(JSON, find_alt_bill_links가 채움)
            "반영된원안목록": "",
        })

    absorbed = [{
        "의안ID": r.get("BILL_ID", ""),
        "법안명": r.get("BILL_NAME", ""),
        "제안자": r.get("PROPOSER", ""),
        "소관위원회": r.get("CURR_COMMITTEE", ""),
        "상임위처리일": r.get("CMT_PROC_DT", ""),
        "처리결과": r.get("CMT_PROC_RESULT_CD") or r.get("PROC_RESULT_CD") or "",
        "상세링크": r.get("LINK_URL", ""),
    } for r in absorbed_rows if r.get("BILL_ID")]
    find_alt_bill_links(bills, absorbed)

    # 전체 법안이 수만 건이라 전부 요약하면 무료 한도를 넘기므로, 최근 발의된 것 중
    # 아직 요약이 없는 법안만 대상으로 한다. 오래된 대기 법안은 요약 없이 남는다.
    to_summarize = [
        b for b in bills
        if not b["AI요약"] and b["제안일"] and b["제안일"] >= summary_cutoff
    ]
    print(f"[법안 요약] 최근 {RECENT_DAYS_FOR_SUMMARY}일 이내 신규/미요약 {len(to_summarize)}건 "
          f"(전체 {len(bills)}건 중 기존 요약 재사용 {sum(1 for b in bills if b['AI요약'])}건)")
    summarize_items = []
    for i, b in enumerate(to_summarize):
        text = fetch_bill_proposal_text(b["의안ID"])
        if text:
            summarize_items.append({"idx": i, "name": b["법안명"], "text": text})
        time.sleep(0.3)
    summary_map = summarize_bills_with_gemini(summarize_items)
    for it in summarize_items:
        to_summarize[it["idx"]]["AI요약"] = summary_map.get(it["idx"], "")

    df = pd.DataFrame(bills)
    df.to_csv(BILL_LIST_PATH, index=False, encoding="utf-8-sig")
    print(f"[입법 수집 완료] {len(bills)}건 -> {BILL_LIST_PATH}")
    append_status_log(bills, prev_bills, fetch_complete, now_str)


if __name__ == "__main__":
    main()
