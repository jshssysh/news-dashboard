"""
법제처 국가법령정보 Open API에서 공정위 의결서/재결서(target=ftc)와 관련 법원
판례(target=prec)를 수집해 decision_list.csv에 누적 저장한다. news_list.csv처럼
한 번 나온 결정문은 사라지지 않는 기록이라, 매번 통째로 새로 받는 게 아니라
"이미 있는 건 건너뛰고 새로 나온 것만" 증분으로 쌓는다.

사용 API (www.law.go.kr/DRF, LAW_GO_KR_OC 필요):
- OC는 법제처에 정식 신청해서 받는 인증키다(신청/문의: 02-2109-6446). 환경변수가
  없으면 데모용 "test"로 대신 호출하는데, 이건 체험용일 뿐 정식 서비스에는 쓰면
  안 된다고 API 안내에 명시돼 있다 - 실제로 GitHub Actions에서 매일 돌리려면
  LAW_GO_KR_OC를 발급받아 시크릿으로 등록해야 한다.
- target=ftc(공정거래위원회 결정문): 이 API 자체가 공정위 결정문 전용이라 키워드
  없이 최신순으로 훑으면서 최근 N일 이내 결정일자만 모은다.
- target=prec(판례, 전체 법원 대상 범용 검색): config/bill_keywords.yaml에 이미
  있는 법률명 키워드(계류법안 분류에 쓰는 것과 같은 목록)로 검색해 관련 판례만
  좁혀 모은다.

이 사건의 "원심"(1심/2심 등)이 무엇인지는 API가 구조화된 링크로 알려주지 않는다 -
실측 결과 <원심결> 같은 필드는 항상 비어 있고, 실제 참조는 본문 자유텍스트 안에만
있으며, 결정번호 자체도 고유키가 아니라(같은 번호가 여러 건에 중복 부여됨) 그걸로
재조회해도 다른 사건이 섞여 나온다. 그래서 심급을 자동으로 잇지 않고, 본문에 그런
문구가 있으면 정규식으로 원문 그대로만 인용한다(extract_prior_instance_ref) -
AI에게 관계를 판단·서술시키지 않는다(대조할 구조화된 필드가 없어 틀려도 못 잡음).

실행: python collect_decisions.py
"""
import os
import re
import time
import traceback
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import requests
import yaml
import pandas as pd

LAW_API_OC = os.environ.get("LAW_GO_KR_OC", "").strip() or "test"
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip().replace('"', '').replace("'", "")
KST = timezone(timedelta(hours=9))

BILL_KEYWORDS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config", "bill_keywords.yaml")
DECISION_LIST_PATH = "decision_list.csv"
LAW_API_BASE = "https://www.law.go.kr/DRF"

# 의결서/판례는 뉴스보다 훨씬 적게 나오므로(일주일에 많아야 수십 건), news_list.csv와
# 달리 보관기간 제한을 두지 않는다 - member_news.csv처럼 무기한 누적해도 GitHub의
# 파일당 100MB 한도에 도달하는 데 몇 년은 걸릴 것으로 추정된다. 다만 "새로 뭘
# 가져올지" 판단할 때는 아래 일수만큼만 최신순으로 훑는다(그 이전 것은 이미 지난
# 실행에서 다 모았다고 가정).
LOOKBACK_DAYS = 30


def post_gemini_with_retry(url, payload, timeout=30, retries=1, retry_wait=5):
    """Gemini 호출을 감싸서, 서버 과부하(503)나 타임아웃처럼 일시적 오류일 때만
    짧게 대기 후 한 번 더 시도한다 (main.py/collect_bills.py의 동일 함수와 같은 목적)."""
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


def get_law_api_with_retry(url, params, timeout=20, retries=2, retry_wait=10):
    """법제처 API 호출을 감싸서, 접속 실패(타임아웃 등)면 짧게 대기 후 재시도한다."""
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


def load_law_keywords():
    """계류법안 분류에 쓰는 법률명 키워드를 그대로 재사용한다 - 이 프로젝트가
    추적하는 법률 범위가 어차피 같기 때문에 목록을 따로 안 둔다.

    "·"(가운뎃점)이 들어간 키워드로 검색하면 법제처 서버가 응답 XML에 그 문자를
    "&middot;"로만 바꾸고 정작 "&"는 이스케이프하지 않아 깨진 XML을 돌려준다
    (실측 확인됨 - 서버 쪽 버그로 보임). 우리 쪽에서 아예 그 문자를 빼고
    검색하면 이 문제를 피할 수 있고, 검색 자체엔 지장이 없다."""
    with open(BILL_KEYWORDS_PATH, "r", encoding="utf-8") as f:
        keywords = list(yaml.safe_load(f)["keywords"].keys())
    return [kw.replace("·", "").replace("ㆍ", "") for kw in keywords]


def parse_kr_date(s):
    """법제처 API의 날짜 표기가 API/목적별로 다 달라서(예: "2026.8.11.", "2026.02.26",
    "20260226") 여러 형식을 순서대로 시도한다."""
    s = (s or "").strip().rstrip(".")
    if not s:
        return None
    for fmt in ("%Y.%m.%d", "%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


# 원심(1심/2심 등)을 언급하는 문장 패턴 - "원심결...제2026-087호"(공정위 재결서),
# "원심판결...선고 2024누12345"(법원 판례) 두 가지 실제 표기를 확인해서 만들었다.
# 못 찾으면 그냥 빈 문자열로 두고(추측 안 함), AI에게도 이 판단을 맡기지 않는다.
PRIOR_INSTANCE_PATTERN = re.compile(
    r"원심(?:결|판결)[^\n]{0,60}?(?:제\s*\d{4}\s*-\s*\d+\s*호|선고\s*\d{4}[가-힣]{1,3}\d+)"
)


def extract_prior_instance_ref(text):
    m = PRIOR_INSTANCE_PATTERN.search(text or "")
    if not m:
        return ""
    return re.sub(r"\s+", " ", m.group(0)).strip()


def _t(elem, path):
    child = elem.find(path)
    return (child.text or "").strip() if child is not None and child.text else ""


def fetch_ftc_list(days=LOOKBACK_DAYS, max_pages=10):
    """공정위 결정문(의결서/재결서) 전체를 최신순으로 훑어 최근 N일 이내만 모은다.
    이 API 자체가 공정위 결정문 전용이라 별도 키워드 검색이 필요 없다."""
    cutoff = datetime.now(KST).date() - timedelta(days=days)
    rows = []
    for page in range(1, max_pages + 1):
        params = {"OC": LAW_API_OC, "target": "ftc", "type": "XML", "display": 100, "page": page, "sort": "ddes"}
        try:
            res = get_law_api_with_retry(f"{LAW_API_BASE}/lawSearch.do", params)
            if res.status_code != 200:
                print(f"[의결서 목록 조회 실패] {page}페이지: status={res.status_code} body={res.text[:200]}")
                break
            root = ET.fromstring(res.content)
            items = root.findall(".//ftc")
            if not items:
                break
            reached_cutoff = False
            for item in items:
                date_str = _t(item, "결정일자")
                d = parse_kr_date(date_str)
                if d and d < cutoff:
                    reached_cutoff = True
                    continue
                rows.append({
                    "id": _t(item, "결정문일련번호"),
                    "사건명": _t(item, "사건명"),
                    "사건번호": _t(item, "사건번호"),
                    "문서유형": _t(item, "문서유형") or "의결서",
                    "날짜": date_str,
                })
            if reached_cutoff or len(items) < 100:
                break
        except Exception:
            print(f"[의결서 목록 조회 예외] {page}페이지:\n{traceback.format_exc()}")
            break
        time.sleep(0.2)
    return rows


def fetch_prec_list(keywords, days=LOOKBACK_DAYS, max_pages=5):
    """관련 법률명 키워드로 판례를 검색해 최근 N일 이내 선고분만 모은다(중복 제거)."""
    cutoff = datetime.now(KST).date() - timedelta(days=days)
    seen_ids = set()
    rows = []
    for kw in keywords:
        for page in range(1, max_pages + 1):
            params = {"OC": LAW_API_OC, "target": "prec", "type": "XML", "query": kw,
                      "search": 2, "display": 100, "page": page, "sort": "ddes"}
            try:
                res = get_law_api_with_retry(f"{LAW_API_BASE}/lawSearch.do", params)
                if res.status_code != 200:
                    print(f"[판례 목록 조회 실패] '{kw}' {page}페이지: status={res.status_code}")
                    break
                root = ET.fromstring(res.content)
                items = root.findall(".//prec")
                if not items:
                    break
                reached_cutoff = False
                for item in items:
                    pid = _t(item, "판례일련번호")
                    if not pid or pid in seen_ids:
                        continue
                    date_str = _t(item, "선고일자")
                    d = parse_kr_date(date_str)
                    if d and d < cutoff:
                        reached_cutoff = True
                        continue
                    seen_ids.add(pid)
                    rows.append({
                        "id": pid,
                        "사건명": _t(item, "사건명"),
                        "사건번호": _t(item, "사건번호"),
                        "법원명": _t(item, "법원명"),
                        "사건종류명": _t(item, "사건종류명"),
                        "날짜": date_str,
                    })
                if reached_cutoff or len(items) < 100:
                    break
            except Exception:
                print(f"[판례 목록 조회 예외] '{kw}' {page}페이지:\n{traceback.format_exc()}")
                break
            time.sleep(0.2)
    return rows


def fetch_ftc_detail(decision_id):
    try:
        res = get_law_api_with_retry(f"{LAW_API_BASE}/lawService.do",
                                      {"OC": LAW_API_OC, "target": "ftc", "ID": decision_id, "type": "XML"})
        if res.status_code != 200:
            return None
        root = ET.fromstring(res.content)
        return {
            "이유": _t(root, "이유"),
            "주문": _t(root, "주문"),
            "피심정보내용": _t(root, "피심정보/피심정보내용"),
        }
    except Exception:
        print(f"[의결서 본문 조회 예외] id={decision_id}:\n{traceback.format_exc()}")
        return None


def fetch_prec_detail(prec_id):
    try:
        res = get_law_api_with_retry(f"{LAW_API_BASE}/lawService.do",
                                      {"OC": LAW_API_OC, "target": "prec", "ID": prec_id, "type": "XML"})
        if res.status_code != 200:
            return None
        root = ET.fromstring(res.content)
        return {
            "판시사항": _t(root, "판시사항"),
            "판결요지": _t(root, "판결요지"),
            "판례내용": _t(root, "판례내용"),
        }
    except Exception:
        print(f"[판례 본문 조회 예외] id={prec_id}:\n{traceback.format_exc()}")
        return None


def analyze_decision_with_gemini(case_name, case_no, body_text):
    """의결서/판례 원문을 받아 2줄 요약 + (있으면) 과징금 또는 형량을 뽑는다.

    원심(하급심)과의 관계는 여기서 절대 판단·서술시키지 않는다 - 대조할 구조화된
    필드가 없어 AI가 틀려도(예: 파기/유지를 반대로 서술) 잡아낼 방법이 없기
    때문이다(extract_prior_instance_ref가 원문 그대로 인용하는 것과 역할을 나눔)."""
    if not GEMINI_API_KEY or not body_text:
        return {"summary": "", "penalty": "", "sentence": ""}
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent?key={GEMINI_API_KEY}"
    prompt = f"""아래는 공정거래위원회 의결서/재결서 또는 법원 판례의 원문 일부입니다.
[사건명] {case_name}
[사건번호] {case_no}
[원문]
{body_text[:6000]}

다음 3가지를 JSON으로 답하세요.
1. summary: 이 사건의 핵심 쟁점과 결과를 1~2문장으로. 정중체("~합니다") 대신
   개조식("~함/~했음/~임")으로 끝낼 것.
   예(좋음): "계약서면 미교부 등으로 시정명령 및 과징금 부과됨"
   예(나쁨): "계약서면을 교부하지 않아 시정명령을 받았습니다"
2. penalty: 과징금·벌금 액수가 원문에 명시돼 있으면 "4억 6,200만원"처럼 사람이
   읽기 쉬운 형태로. 명시돼 있지 않으면 빈 문자열.
3. sentence: 징역·집행유예 등 형사처벌 형량이 원문에 명시돼 있으면 그대로(예:
   "징역 1년, 집행유예 2년"). 없으면 빈 문자열.

주의: 원심/하급심과의 관계, 이 사건이 확정됐는지 여부는 절대 판단하거나 summary에
넣지 마세요 - 원문에 실제로 적힌 조치·처벌 내용만 요약하세요.

응답 형식: {{"summary": "...", "penalty": "...", "sentence": "..."}}
"""
    try:
        payload = {"contents": [{"parts": [{"text": prompt}]}],
                   "generationConfig": {"response_mime_type": "application/json",
                                         "thinkingConfig": {"thinkingLevel": "low"}}}
        res = post_gemini_with_retry(url, payload)
        if res.status_code == 200:
            raw = res.json()['candidates'][0]['content']['parts'][0]['text'].strip()
            if raw.startswith("```json"):
                raw = raw[7:]
            if raw.startswith("```"):
                raw = raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3]
            import json
            data = json.loads(raw.strip())
            return {"summary": data.get("summary", ""), "penalty": data.get("penalty", ""),
                    "sentence": data.get("sentence", "")}
        print(f"[결정문 AI 요약 오류] status={res.status_code} body={res.text[:200]}")
    except Exception:
        print(f"[결정문 AI 요약 예외]\n{traceback.format_exc()}")
    return {"summary": "", "penalty": "", "sentence": ""}


def main():
    if LAW_API_OC == "test":
        print("[경고] LAW_GO_KR_OC가 설정되지 않아 데모키(test)로 호출합니다 - "
              "체험용이라 정식 서비스에는 부적절하니 법제처에 정식 OC를 발급받아 "
              "시크릿으로 등록하세요(신청/문의 02-2109-6446).")

    existing = {}
    if os.path.exists(DECISION_LIST_PATH) and os.path.getsize(DECISION_LIST_PATH) > 0:
        try:
            odf = pd.read_csv(DECISION_LIST_PATH, dtype=str, keep_default_na=False)
        except Exception:
            odf = pd.DataFrame()
    else:
        odf = pd.DataFrame()
    if not odf.empty:
        for _, r in odf.iterrows():
            existing[(r["구분"], r["id"])] = True

    keywords = load_law_keywords()
    ftc_rows = fetch_ftc_list()
    prec_rows = fetch_prec_list(keywords)
    print(f"[법제처 수집] 의결서/재결서 {len(ftc_rows)}건, 판례 {len(prec_rows)}건 조회(최근 {LOOKBACK_DAYS}일)")

    new_records = []

    for row in ftc_rows:
        key = (row["문서유형"], row["id"])
        if key in existing:
            continue
        detail = fetch_ftc_detail(row["id"])
        if detail is None:
            continue
        body = detail["이유"] or detail["주문"]
        prior_ref = extract_prior_instance_ref(detail["이유"] + " " + detail["피심정보내용"])
        analysis = analyze_decision_with_gemini(row["사건명"], row["사건번호"], body)
        new_records.append({
            "구분": row["문서유형"], "id": row["id"], "사건명": row["사건명"],
            "사건번호": row["사건번호"], "날짜": row["날짜"], "기관법원": "공정거래위원회",
            "사건종류": "", "AI요약": analysis["summary"], "과징금": analysis["penalty"],
            "형량": analysis["sentence"], "원심참조": prior_ref,
            "상세링크": f"https://www.law.go.kr/DRF/lawService.do?OC={LAW_API_OC}&target=ftc&ID={row['id']}&type=HTML",
        })
        time.sleep(4.5)  # 무료 등급은 분당 15회 제한

    for row in prec_rows:
        key = ("판례", row["id"])
        if key in existing:
            continue
        detail = fetch_prec_detail(row["id"])
        if detail is None:
            continue
        body = (detail["판결요지"] or detail["판시사항"]) + " " + detail["판례내용"]
        prior_ref = extract_prior_instance_ref(detail["판례내용"])
        analysis = analyze_decision_with_gemini(row["사건명"], row["사건번호"], body)
        new_records.append({
            "구분": "판례", "id": row["id"], "사건명": row["사건명"],
            "사건번호": row["사건번호"], "날짜": row["날짜"], "기관법원": row["법원명"],
            "사건종류": row["사건종류명"], "AI요약": analysis["summary"],
            "과징금": "", "형량": analysis["sentence"], "원심참조": prior_ref,
            "상세링크": f"https://www.law.go.kr/DRF/lawService.do?OC={LAW_API_OC}&target=prec&ID={row['id']}&type=HTML",
        })
        time.sleep(4.5)

    if not new_records:
        print("[법제처 수집 완료] 새로 추가할 건 없음")
        return

    new_df = pd.DataFrame(new_records)
    if not odf.empty:
        combined = pd.concat([odf, new_df], ignore_index=True)
    else:
        combined = new_df
    combined = combined.drop_duplicates(subset=["구분", "id"], keep="last")
    combined = combined.sort_values("날짜", ascending=False)
    combined.to_csv(DECISION_LIST_PATH, index=False, encoding="utf-8-sig")
    print(f"[법제처 수집 완료] 신규 {len(new_records)}건 추가 → 누적 {len(combined)}건")


if __name__ == "__main__":
    main()
