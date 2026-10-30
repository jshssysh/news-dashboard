"""
국회 열린국회정보 Open API 중 "본회의 이후(정부 이송·공포)" 정보를 줄 만한 서비스를
한 번씩 호출해 응답 구조와 필드 이름을 로그로 찍는 확인용 스크립트.

collect_bills.py가 쓰는 계류의안 API(nwbqublzajtcqpdae)는 본회의를 통과한 법안을
안 주므로, 입법 탭 단계 줄의 본회의 옆에 "공포" 칩을 붙이려면 공포일·공포번호를
주는 다른 서비스가 필요하다. 회사 PC에서는 open.assembly.go.kr이 막혀 있어서
(2026-10-27) 서비스 ID와 필드 이름을 확인하지 못했다 - 그래서 GitHub Actions에서
probe-assembly-api.yml로 이 스크립트를 수동 실행해 로그로 확인한다.
데이터를 저장하거나 커밋하지 않는다.

아래 서비스 ID 중 일부는 문서로 확인하지 못한 추정값이다 - 없는 ID면 오류 코드만
찍히고 넘어간다.

의안번호를 쉼표로 주면(BILL_NOS) 위 확인 대신 collect_bills.py가 쓰는 세 서비스를
22대 전체로 끝까지 훑어서 그 의안번호의 행을 통째로 찍는다. 국회 의안 목록에는
공포됐는데 bill_list.csv에 없는 법안이 왜 빠졌는지 보려는 용도다(2026-10-30).

실행: python probe_assembly_api.py [법안명 일부(선택, 예: 자본시장)]
      BILL_NOS=2203518,2203522 python probe_assembly_api.py
      BILL_IDS=PRC_xxx,ARC_xxx python probe_assembly_api.py  (likms 의안 상세 페이지 확인)
"""
import json
import os
import re
import sys
import time
from collections import Counter

import requests

ASSEMBLY_API_KEY = os.environ.get("ASSEMBLY_API_KEY", "").strip().replace('"', '').replace("'", "")
BASE_URL = "https://open.assembly.go.kr/portal/openapi"

CANDIDATES = [
    ("ALLBILL", "의안정보 통합"),
    ("TVBPMBILL11", "법률안 심사 및 처리(의안검색)"),
    ("nzpltgfqabtcpsmai", "처리의안(추정)"),
    ("nwbpacrgavhjryiph", "본회의 처리안건 - 법률안(추정)"),
    ("nzmimeepazxkubdpn", "국회의원 발의법률안"),
]

# 서비스마다 대수(代數) 파라미터 이름이 달라서 몇 가지를 돌아가며 넣어 본다.
PARAM_VARIANTS = [
    {"AGE": "22"},
    {"ERACO": "제22대"},
    {},
]

# 공포 관련으로 보이는 필드를 따로 모아 보여주기 위한 이름 조각.
PROM_HINTS = ("PROM", "GVRN", "TRSF", "RGS", "PROC")


def call(service_id, extra, p_size=5):
    params = {"Type": "json", "pIndex": 1, "pSize": p_size, **extra}
    if ASSEMBLY_API_KEY:
        params["KEY"] = ASSEMBLY_API_KEY
    res = requests.get(f"{BASE_URL}/{service_id}", params=params, timeout=30)
    try:
        return res.status_code, res.json(), ""
    except ValueError:
        return res.status_code, None, res.text[:300]


def split_payload(payload, service_id):
    """공통 응답({서비스ID: [{head:[...]}, {row:[...]}]}) 또는 오류({RESULT:{...}})를
    (총건수, 결과코드/메시지, rows)로 나눈다."""
    if not isinstance(payload, dict):
        return None, "", []
    if "RESULT" in payload:
        r = payload["RESULT"]
        return None, f"{r.get('CODE')} {r.get('MESSAGE')}", []
    blocks = payload.get(service_id)
    if blocks is None and len(payload) == 1:
        blocks = next(iter(payload.values()))
    total, result, rows = None, "", []
    for block in blocks or []:
        if not isinstance(block, dict):
            continue
        for h in block.get("head", []):
            if "list_total_count" in h:
                total = h["list_total_count"]
            if "RESULT" in h:
                result = f"{h['RESULT'].get('CODE')} {h['RESULT'].get('MESSAGE')}"
        if "row" in block:
            rows = block["row"] or []
    return total, result, rows


def show_rows(rows, limit=2):
    print(f"    필드({len(rows[0])}개): {', '.join(rows[0].keys())}")
    for row in rows[:limit]:
        hinted = {k: v for k, v in row.items() if any(h in k for h in PROM_HINTS)}
        print(f"    [예시] {row.get('BILL_NAME') or row.get('BILL_NM') or ''} ({row.get('BILL_NO', '')})")
        print(f"      공포·처리 관련 필드: {json.dumps(hinted, ensure_ascii=False)}")
    print("    [첫 행 전체]")
    print("      " + json.dumps(rows[0], ensure_ascii=False))


# collect_bills.py와 같은 서비스·파라미터. 계류의안은 대수 파라미터 없이 부른다.
SCAN_SERVICES = [
    ("nwbqublzajtcqpdae", "계류의안", {}),
    ("nwbpacrgavhjryiph", "본회의 처리안건 - 법률안", {"AGE": "22"}),
    ("nzpltgfqabtcpsmai", "처리의안", {"AGE": "22"}),
]


def scan_bill_nos(bill_nos):
    for service_id, label, extra in SCAN_SERVICES:
        print(f"\n===== {service_id} ({label}) 전체 훑기 =====")
        rows, p_index = [], 1
        while p_index <= 300:
            try:
                status, payload, raw = call(service_id, {**extra, "pIndex": p_index}, p_size=100)
            except requests.exceptions.RequestException as e:
                print(f"  {p_index}페이지: 접속 실패 {type(e).__name__}")
                break
            total, result, page = split_payload(payload, service_id)
            if p_index == 1:
                print(f"  HTTP {status}, 결과 {result or '-'}, 총 {total}건")
            if not page:
                break
            rows.extend(page)
            if len(page) < 100:
                break
            p_index += 1
            time.sleep(0.1)
        print(f"  받은 행 {len(rows)}건")
        for key in ("PROC_RESULT_CD", "CMT_PROC_RESULT_CD", "LAW_PROC_RESULT_CD"):
            if rows and key in rows[0]:
                print(f"  {key} 값별 건수: {dict(Counter(r.get(key) or '' for r in rows))}")
        found = {}
        for r in rows:
            if str(r.get("BILL_NO", "")) in bill_nos:
                found.setdefault(str(r["BILL_NO"]), []).append(r)
        for no in bill_nos:
            hits = found.get(no, [])
            print(f"  [{no}] {len(hits)}건")
            for r in hits:
                print("      " + json.dumps(r, ensure_ascii=False))


LIKMS_DETAIL_URL = "https://likms.assembly.go.kr/bill/billDetail.do"
DETAIL_HINTS = ("재의", "공포", "정부이송", "이송", "부결", "가결", "진행", "단계")


def probe_bill_details(bill_ids):
    """likms 의안 상세 페이지에서 거부권(재의요구)과 공포를 가를 문구가 있는지 본다.
    처리의안 API로는 둘이 똑같이 '원안가결'이라 구분이 안 된다(2026-10-30 실측).
    페이지가 자바스크립트로 내용을 채울 수 있어서, 상태 문구 줄과 함께 페이지가
    부르는 .do 주소도 찍는다."""
    for bill_id in bill_ids:
        print(f"\n===== likms 상세 {bill_id} =====")
        try:
            res = requests.get(LIKMS_DETAIL_URL, params={"billId": bill_id}, timeout=30)
        except requests.exceptions.RequestException as e:
            print(f"  접속 실패 {type(e).__name__}")
            continue
        html = res.text
        print(f"  HTTP {res.status_code}, {len(html)}자")
        body = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", html)
        text = re.sub(r"<[^>]+>", "\n", body)
        lines = [re.sub(r"\s+", " ", t).strip() for t in text.splitlines()]
        lines = [t for t in lines if t]
        hits = [(i, t) for i, t in enumerate(lines) if any(h in t for h in DETAIL_HINTS)]
        print(f"  상태 문구 줄 {len(hits)}개 (앞뒤 한 줄씩)")
        for i, t in hits[:40]:
            around = " | ".join(lines[max(0, i - 1):i + 2])
            print(f"    {around[:200]}")
        urls = sorted(set(re.findall(r"[\w/.-]+\.do", html)))
        print(f"  페이지 안의 .do 주소: {', '.join(urls)}")


def main():
    bill_ids = [n.strip() for n in os.environ.get("BILL_IDS", "").split(",") if n.strip()]
    if bill_ids:
        probe_bill_details(bill_ids)
        return
    bill_nos =[n.strip() for n in os.environ.get("BILL_NOS", "").split(",") if n.strip()]
    if bill_nos:
        scan_bill_nos(bill_nos)
        return
    name_filter = sys.argv[1].strip() if len(sys.argv) > 1 and sys.argv[1].strip() else ""
    if not ASSEMBLY_API_KEY:
        print("[경고] ASSEMBLY_API_KEY가 없습니다 - 키 없이 호출합니다(샘플 건수만 오거나 실패할 수 있음).")

    for service_id, label in CANDIDATES:
        print(f"\n===== {service_id} ({label}) =====")
        for extra in PARAM_VARIANTS:
            try:
                status, payload, raw = call(service_id, extra)
            except requests.exceptions.RequestException as e:
                # 예외 문구에는 KEY가 든 요청 주소가 통째로 들어 있어서 종류만 찍는다.
                print(f"  파라미터 {extra}: 접속 실패 {type(e).__name__}")
                continue
            if payload is None:
                print(f"  파라미터 {extra}: HTTP {status}, JSON 아님: {raw!r}")
                continue
            total, result, rows = split_payload(payload, service_id)
            print(f"  파라미터 {extra}: HTTP {status}, 결과 {result or '-'}, 총 {total}건, 이번 {len(rows)}건")
            if not rows:
                continue
            show_rows(rows)
            # 공포까지 간 법안 예시를 보려고 법안명으로 한 번 더 좁혀 본다.
            if name_filter:
                for key in ("BILL_NAME", "BILL_NM"):
                    if key not in rows[0]:
                        continue
                    try:
                        _, p2, _ = call(service_id, {**extra, key: name_filter}, p_size=10)
                    except requests.exceptions.RequestException as e:
                        print(f"  {key}={name_filter}: 접속 실패 {type(e).__name__}")
                        break
                    t2, r2, rows2 = split_payload(p2, service_id)
                    print(f"  {key}={name_filter}: 결과 {r2 or '-'}, 총 {t2}건")
                    if rows2:
                        show_rows(rows2, limit=5)
                    break
            break  # 이 서비스는 이 파라미터로 응답이 왔으니 다음 서비스로


if __name__ == "__main__":
    main()
