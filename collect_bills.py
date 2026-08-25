"""
국회 열린국회정보 Open API에서 국회 전체 계류 법안을 수집해 bill_list.csv로 저장한다.

사용 API (open.assembly.go.kr, ASSEMBLY_API_KEY 필요):
- 계류의안(nwbqublzajtcqpdae): 전체 계류 법안 목록. 소관위/법사위 처리 단계 필드가
  응답에 이미 포함돼 있어서, 법안마다 상세조회를 따로 할 필요가 없다. 본회의 회부를
  직접 알려주는 날짜 필드는 없지만, 법사위 결과가 원안가결/수정가결이면 본회의
  표결 대기로 넘어간 것으로 간접 판단한다(derive_stage_and_result 참고). 정부
  이송/공포는 이 API에 아예 없음 - "계류"(아직 안 끝난) 법안만 주는 API라 그
  단계까지 간 법안은 여기 안 나온다.
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
실행: python collect_bills.py
"""
import json
import os
import re
import time
import requests
import yaml
import pandas as pd
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone

ASSEMBLY_API_KEY = os.environ.get("ASSEMBLY_API_KEY", "").strip().replace('"', '').replace("'", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip().replace('"', '').replace("'", "")
KST = timezone(timedelta(hours=9))

BILL_KEYWORDS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config", "bill_keywords.yaml")
BILL_LIST_PATH = "bill_list.csv"

BASE_URL = "https://open.assembly.go.kr/portal/openapi"
PENDING_BILL_API = "nwbqublzajtcqpdae"  # 계류의안
MEMBER_INFO_API = "ALLNAMEMBER"  # 국회의원 인적사항 (정당/선수/지역구)
BILL_SUMMARY_URL = "https://likms.assembly.go.kr/bill/bi/popup/billSummary.do"

# 전체 법안이 수만 건이라, 이 기간 이내 발의된 것만 AI 한줄요약을 만든다
# (오래된 대기 법안은 법안명/상태/링크만 표시되고 요약은 "준비 중"으로 남음)
RECENT_DAYS_FOR_SUMMARY = 30


def post_gemini_with_retry(url, payload, timeout=30, retries=1, retry_wait=5):
    """Gemini 호출을 감싸서, 서버 과부하(503)나 타임아웃처럼 일시적 오류일 때만
    짧게 대기 후 한 번 더 시도한다 (main.py의 동일 함수와 같은 목적)."""
    last_exc = None
    for attempt in range(retries + 1):
        try:
            res = requests.post(url, json=payload, timeout=timeout)
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
COMMITTEE_ABBR = {
    "국회운영위원회": "운영위", "법제사법위원회": "법사위", "정무위원회": "정무위",
    "재정경제기획위원회": "재정경제기획위", "교육위원회": "교육위", "과학기술정보방송통신위원회": "과방위",
    "외교통일위원회": "외통위", "국방위원회": "국방위", "행정안전위원회": "행안위",
    "문화체육관광위원회": "문체위", "농림축산식품해양수산위원회": "농해수위",
    "산업통상자원중소벤처기업위원회": "산자위", "보건복지위원회": "복지위",
    "기후에너지환경노동위원회": "기후에너지환경노동위", "국토교통위원회": "국토위", "정보위원회": "정보위",
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
    뜨는 일이 없게 한다. 위원회 정보 자체가 없으면(미배정) 그때만 "기타"."""
    for keyword, category in keywords_map.items():
        if keyword in bill_name:
            return category
    return committee_abbr(committee) or "기타"


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


def fetch_all_pending_bills():
    """계류의안 API로 국회 전체 계류 법안을 페이지 단위로 다 가져온다 (BILL_NAME 필터 없음)."""
    results = []
    p_index = 1
    while p_index <= 300:  # 안전장치: 무한 루프 방지 (최대 3만 건)
        params = {"Type": "json", "pIndex": p_index, "pSize": 100}
        if ASSEMBLY_API_KEY:
            params["KEY"] = ASSEMBLY_API_KEY
        try:
            res = requests.get(f"{BASE_URL}/{PENDING_BILL_API}", params=params, timeout=15)
            rows = _extract_rows(res.json(), PENDING_BILL_API)
            if not rows:
                if p_index == 1:
                    print(f"[계류의안 전체조회 - 결과 없음] status={res.status_code} body={res.text[:300]}")
                break
            results.extend(rows)
            if p_index % 20 == 0:
                print(f"[계류의안 전체조회] {p_index}페이지까지 누적 {len(results)}건")
            if len(rows) < 100:
                break
            p_index += 1
            time.sleep(0.1)
        except Exception as e:
            print(f"[계류의안 전체조회 예외] {p_index}페이지: {e}")
            break
    return results


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


def fetch_member_info():
    """국회의원 인적사항 API에서 이름 -> {정당, 이전정당, 지역구, 선수} 사전을 만든다.
    한 사람당 한 행에 그동안 거쳐온 소속정당이 '/'로 이어져 있어서(예: "더불어민주당/조국혁신당"),
    마지막 조각을 현재 정당으로, 그 바로 앞 조각을 이전 정당으로 쓴다."""
    info = {}
    p_index = 1
    while p_index <= 50:  # 안전장치: 전체 역대 의원 약 3300명 수준이면 충분
        params = {"Type": "json", "pIndex": p_index, "pSize": 100}
        if ASSEMBLY_API_KEY:
            params["KEY"] = ASSEMBLY_API_KEY
        try:
            res = requests.get(f"{BASE_URL}/{MEMBER_INFO_API}", params=params, timeout=15)
            rows = _extract_rows(res.json(), MEMBER_INFO_API)
            if not rows:
                break
            for row in rows:
                name = row.get("NAAS_NM")
                if not name:
                    continue
                parties = [p.strip() for p in (row.get("PLPT_NM") or "").split("/") if p.strip()]
                info[name] = {
                    "party": parties[-1] if parties else "",
                    "prev_party": parties[-2] if len(parties) >= 2 else "",
                    # 지역구가 비어있으면(API가 안 채워준 경우도 포함) 지역구 없는
                    # 의원은 곧 비례대표라는 뜻이라 "비례대표"라고 명시한다.
                    "district": (row.get("ELECD_NM") or "").split("/")[-1].strip() or "비례대표",
                    "term": row.get("RLCT_DIV_NM") or "",
                }
            if len(rows) < 100:
                break
            p_index += 1
            time.sleep(0.1)
        except Exception as e:
            print(f"[국회의원 인적사항 조회 예외] {p_index}페이지: {e}")
            break
    return info


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
    애초에 여기 나오지 않기 때문."""
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


def load_previous_status():
    """직전 실행 결과(bill_list.csv)에서 {의안ID: 처리상태}를 읽어온다 (상태변경 감지용)."""
    if not os.path.exists(BILL_LIST_PATH):
        return {}
    try:
        df = pd.read_csv(BILL_LIST_PATH)
        return dict(zip(df["의안ID"], df["처리상태"]))
    except Exception as e:
        print(f"[이전 bill_list.csv 로드 실패] {e}")
        return {}


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
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent?key={GEMINI_API_KEY}"
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
    rows = fetch_all_pending_bills()
    print(f"[계류의안 전체조회 완료] 총 {len(rows)}건")

    member_info = fetch_member_info()
    print(f"[국회의원 인적사항 조회 완료] {len(member_info)}명")

    seen = {}
    for row in rows:
        bill_id = row.get("BILL_ID")
        if not bill_id or bill_id in seen:
            continue
        seen[bill_id] = row

    prev_status = load_previous_status()

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
            "상태변경": changed,
            "상세링크": row.get("LINK_URL", ""),
            "AI요약": prev_summaries.get(bill_id, ""),
            "최종수집일": now_str,
        })

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


if __name__ == "__main__":
    main()
