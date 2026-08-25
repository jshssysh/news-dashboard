"""
국회 열린국회정보 Open API에서 공정위 소관 법률 관련 계류 법안을 수집해 bill_list.csv로 저장한다.

사용 API (둘 다 open.assembly.go.kr, ASSEMBLY_API_KEY 필요):
- 계류의안(nwbqublzajtcqpdae): 법안명 키워드로 검색 -> 의안ID/번호/제안자/제안일/링크
- 의안 상세정보(BILLINFODETAIL): 의안ID로 조회 -> 소관위/법사위/본회의/공포 단계별 처리 정보

API 자체엔 제안이유/주요내용(법안 본문) 필드가 없지만, likms.assembly.go.kr의
"제안이유 요약" 팝업 페이지(billSummary.do)는 자바스크립트 없이도 해당 텍스트를
그대로 담고 있어서, 이 페이지를 긁어와 Gemini로 한 줄 요약해 붙인다.
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
BILL_DETAIL_API = "BILLINFODETAIL"      # 의안 상세정보(심사 단계)
BILL_SUMMARY_URL = "https://likms.assembly.go.kr/bill/bi/popup/billSummary.do"


def load_bill_keywords():
    with open(BILL_KEYWORDS_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)["keywords"]


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


def search_pending_bills(bill_name_keyword):
    """계류의안 API로 법안명에 키워드가 포함된 의안 목록을 가져온다 (페이지네이션 포함)."""
    results = []
    p_index = 1
    while p_index <= 20:  # 안전장치: 무한 루프 방지 (키워드당 최대 2000건)
        params = {"Type": "json", "pIndex": p_index, "pSize": 100, "BILL_NAME": bill_name_keyword}
        if ASSEMBLY_API_KEY:
            params["KEY"] = ASSEMBLY_API_KEY
        try:
            res = requests.get(f"{BASE_URL}/{PENDING_BILL_API}", params=params, timeout=15)
            rows = _extract_rows(res.json(), PENDING_BILL_API)
            if not rows:
                if p_index == 1:
                    # 원인 진단용: 정상이면 빈 결과가 나올 리 없는 첫 페이지가 비었을 때만 원문을 남긴다
                    print(f"[계류의안 검색 - 결과 없음] '{bill_name_keyword}' status={res.status_code} body={res.text[:300]}")
                break
            results.extend(rows)
            if len(rows) < 100:
                break
            p_index += 1
        except Exception as e:
            print(f"[계류의안 검색 예외] '{bill_name_keyword}': {e}")
            break
    return results


def get_bill_process_detail(bill_id):
    """BILLINFODETAIL API로 심사 단계(소관위/법사위/본회의/공포) 정보를 가져온다."""
    params = {"Type": "json", "BILL_ID": bill_id}
    if ASSEMBLY_API_KEY:
        params["KEY"] = ASSEMBLY_API_KEY
    try:
        res = requests.get(f"{BASE_URL}/{BILL_DETAIL_API}", params=params, timeout=15)
        rows = _extract_rows(res.json(), BILL_DETAIL_API)
        if rows:
            return rows[0]
    except Exception as e:
        print(f"[의안상세 조회 예외] {bill_id}: {e}")
    return {}


def derive_status(detail):
    """심사 단계 필드를 보고 사람이 읽기 쉬운 처리상태 문자열을 만든다.
    (실제 국회 절차 순서: 소관위 회부 -> 소관위 심사 -> 소관위 의결 -> 법사위 -> 본회의 -> 공포)"""
    if detail.get("PROM_DT"):
        return "공포"
    if detail.get("RGS_CONF_RSLT"):
        return f"본회의 {detail['RGS_CONF_RSLT']}"
    if detail.get("RGS_PRSNT_DT"):
        return "본회의 부의"
    if detail.get("LAW_PROC_RSLT"):
        return f"법사위 {detail['LAW_PROC_RSLT']}"
    if detail.get("LAW_PRSNT_DT"):
        return "법사위 심사중"
    if detail.get("JRCMIT_PROC_RSLT"):
        return f"소관위 {detail['JRCMIT_PROC_RSLT']}"
    if detail.get("JRCMIT_CMMT_DT"):
        return "소관위 심사중"
    return "소관위 회부"


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
    batches = [items[i:i + 20] for i in range(0, len(items), 20)]
    for batch in batches:
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
            res = requests.post(url, json=payload, timeout=30)
            if res.status_code == 200:
                res_json = res.json()
                raw_text = res_json['candidates'][0]['content']['parts'][0]['text'].strip()
                if raw_text.startswith("```json"): raw_text = raw_text[7:]
                if raw_text.startswith("```"): raw_text = raw_text[3:]
                if raw_text.endswith("```"): raw_text = raw_text[:-3]
                for item in json.loads(raw_text.strip()):
                    i = item.get("idx")
                    if i is not None:
                        results[i] = (item.get("summary") or "").strip()
            else:
                print(f"[법안 요약 오류] status={res.status_code} body={res.text[:300]}")
        except Exception as e:
            print(f"[법안 요약 예외] {e}")
        time.sleep(4.5)  # 무료 등급은 분당 15회 제한
    return results


def main():
    if not ASSEMBLY_API_KEY:
        print("[경고] ASSEMBLY_API_KEY가 없습니다 - 키 없이 호출을 시도합니다 (제한/실패 가능).")

    keywords = load_bill_keywords()
    seen = {}
    for keyword, category in keywords.items():
        rows = search_pending_bills(keyword)
        print(f"[계류의안 검색] '{keyword}' -> {len(rows)}건")
        for row in rows:
            bill_id = row.get("BILL_ID")
            if not bill_id or bill_id in seen:
                continue
            seen[bill_id] = {"row": row, "category": category}
        time.sleep(0.3)

    prev_status = load_previous_status()

    # 안전장치: API 장애/키 문제/일시적 오류 등으로 이번에 새로 가져온 건수가 기존 대비
    # 크게 줄었으면(예: 0건), 그 결과로 기존 bill_list.csv를 덮어쓰지 않고 그대로 둔다.
    # (이전에 이 검사가 없어서 API가 실패한 회차에 287건이 0건으로 날아간 적이 있었음)
    if prev_status and len(seen) < len(prev_status) * 0.3:
        print(f"[경고] 이번 수집 결과({len(seen)}건)가 기존({len(prev_status)}건)보다 크게 적습니다. "
              f"API 오류로 의심되어 bill_list.csv를 덮어쓰지 않고 종료합니다.")
        return

    prev_summaries = load_previous_summaries()
    now_str = datetime.now(KST).strftime("%Y-%m-%d %H:%M")

    bills = []
    for bill_id, info in seen.items():
        row = info["row"]
        detail = get_bill_process_detail(bill_id)
        status = derive_status(detail)
        is_new = bill_id not in prev_status
        changed = "" if is_new else ("변경" if prev_status[bill_id] != status else "")
        bills.append({
            "의안ID": bill_id,
            "의안번호": row.get("BILL_NO", ""),
            "법안명": row.get("BILL_NAME", ""),
            "카테고리": info["category"],
            "제안자": row.get("PROPOSER", ""),
            "대표발의자": row.get("RST_PROPOSER", ""),
            "제안일": row.get("PROPOSE_DT", ""),
            "소관위원회": detail.get("JRCMIT_NM") or row.get("CURR_COMMITTEE") or "",
            "처리상태": status,
            "상태변경": changed,
            "상세링크": row.get("LINK_URL", ""),
            "AI요약": prev_summaries.get(bill_id, ""),
            "최종수집일": now_str,
        })
        time.sleep(0.3)

    # 이미 요약이 있는(제안이유가 안 바뀌는) 법안은 재사용하고, 새로 생긴 법안만 긁어서 요약한다
    to_summarize = [b for b in bills if not b["AI요약"]]
    print(f"[법안 요약] 신규/미요약 {len(to_summarize)}건 (전체 {len(bills)}건 중 기존 요약 재사용 {len(bills)-len(to_summarize)}건)")
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
