"""
국회 열린국회정보 Open API에서 공정위 소관 법률 관련 계류 법안을 수집해 bill_list.csv로 저장한다.

사용 API (둘 다 open.assembly.go.kr, ASSEMBLY_API_KEY 필요):
- 계류의안(nwbqublzajtcqpdae): 법안명 키워드로 검색 -> 의안ID/번호/제안자/제안일/링크
- 의안 상세정보(BILLINFODETAIL): 의안ID로 조회 -> 소관위/법사위/본회의/공포 단계별 처리 정보

API 자체엔 제안이유/주요내용(법안 본문) 필드가 없어서, 대시보드에는 처리상태와
공식 상세페이지 링크까지만 담는다. 실행: python collect_bills.py
"""
import os
import time
import requests
import yaml
import pandas as pd
from datetime import datetime, timedelta, timezone

ASSEMBLY_API_KEY = os.environ.get("ASSEMBLY_API_KEY", "").strip().replace('"', '').replace("'", "")
KST = timezone(timedelta(hours=9))

BILL_KEYWORDS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config", "bill_keywords.yaml")
BILL_LIST_PATH = "bill_list.csv"

BASE_URL = "https://open.assembly.go.kr/portal/openapi"
PENDING_BILL_API = "nwbqublzajtcqpdae"  # 계류의안
BILL_DETAIL_API = "BILLINFODETAIL"      # 의안 상세정보(심사 단계)


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
    now_str = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    out_rows = []
    for bill_id, info in seen.items():
        row = info["row"]
        detail = get_bill_process_detail(bill_id)
        status = derive_status(detail)
        is_new = bill_id not in prev_status
        changed = "" if is_new else ("변경" if prev_status[bill_id] != status else "")
        out_rows.append({
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
            "최종수집일": now_str,
        })
        time.sleep(0.3)

    df = pd.DataFrame(out_rows)
    df.to_csv(BILL_LIST_PATH, index=False, encoding="utf-8-sig")
    print(f"[입법 수집 완료] {len(out_rows)}건 -> {BILL_LIST_PATH}")


if __name__ == "__main__":
    main()
