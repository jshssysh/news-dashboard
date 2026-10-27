"""
법제처 국가법령정보 Open API(target=eflaw, "시행일 법령")에서 공정거래위원회
소관 법률·시행령·시행규칙의 공포·시행 이력을 받아 subordinate_law_list.csv로
저장한다(파일명은 시행령·시행규칙만 받던 때 이름 그대로). 입법 탭의 "법령
공포·시행" 패널이 이 파일을 보여준다.

시행령·시행규칙은 국회를 거치지 않아서 collect_bills.py의 계류의안 API에는
아예 안 나온다. 그래서 "공포된 것"만 법제처에서 따로 가져온다 - 입법예고·
규제심사·국무회의 같은 공포 전 단계는 정리된 공개 API가 없어 다루지 않는다
(2026-10-26 사용자와 확인). 법률도 본회의 의결 뒤에는 계류의안 목록에서
빠져서 공포·시행일을 알 수 없으므로, 같은 목록에 섞여 오는 법률도 함께 담아
"공포 -> 시행" 이행 점검 목록으로 쓴다(2026-10-27 사용자 요청).

collect_law_penalties.py가 쓰는 target=law는 "현행" 한 건씩만 주지만,
target=eflaw에 nw=1,2,3(연혁+시행예정+현행)을 주면 과거 개정 이력과 아직
시행 전인 개정까지 다 나온다(2026-10-26 실측: org=1130000 전체 1,100건).
한 번 공포된 개정이 시행일을 여러 개 가지면(예: 하도급법 시행령 2026.8.4
공포 -> 일부 조항 공포일 시행, 나머지 8.11 시행) 같은 법령일련번호(MST)로
시행일자만 다른 행이 여러 개 오므로, MST 하나를 "공포 1건"으로 묶는다.

사용 API (www.law.go.kr/DRF, LAW_GO_KR_OC 필요 - collect_decisions.py와 동일):
- lawSearch.do?target=eflaw&org=1130000&nw=1,2,3&sort=ddes: 공포일자 내림차순
  목록. 법률·대통령령·총리령이 섞여 온다.
- lawService.do?target=eflaw&MST=<일련번호>&efYd=<시행일자>: 제개정이유·개정문.
  efYd를 빼면 XML 대신 오류 HTML이 온다(실측). 이유 원문에 이미 "◇ 주요내용
  가. 나. ..." 목록이 있어서 Gemini 요약 없이 그 제목만 뽑아 쓴다. 한 번
  받은 이유는 바뀌지 않으므로 다음 실행부터는 기존 CSV 값을 재사용한다.

타법개정은 제개정이유가 "다른 법령"(예: 전남광주통합특별시 특별법 시행령)의
이유라 그대로 보여주면 엉뚱하다 - 개정문 부칙에서 이 법령을 고친 문단만
잘라 보여준다.

공정위 직제(공정거래위원회와 그 소속기관 직제, 같은 시행규칙)는 내부 조직
개편이라 기업이 지킬 규정이 아니므로 뺀다.

공포는 주 몇 건 수준이라 하루 1번(06시대 실행)만 수집한다 - 목록 1~2번 +
새로 공포된 것만 본문 조회라 API 호출도 적다.

실행: python collect_subordinate_laws.py
"""
import os
import re
import time
import traceback
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import requests
import pandas as pd

LAW_API_OC = os.environ.get("LAW_GO_KR_OC", "").strip() or "test"
KST = timezone(timedelta(hours=9))

SUBORDINATE_LAW_LIST_PATH = "subordinate_law_list.csv"
LAW_API_BASE = "https://www.law.go.kr/DRF"
# OC 없이 누구나 열리는 법제처 본문 링크(해당 공포본 기준) - 실측 200 확인.
PUBLIC_LAW_LINK = "https://www.law.go.kr/LSW/lsInfoP.do?lsiSeq={mst}"

FTC_ORG_CODE = "1130000"
# 공포일 기준 최근 2년만 보여준다(22대 국회 임기와 비슷한 범위).
LOOKBACK_DAYS = 730
EXCLUDE_NAME_KEYWORDS = ["직제"]

CSV_COLUMNS = [
    "법령일련번호", "법령ID", "법령명", "법령약칭", "법령종류", "제개정구분",
    "공포일자", "공포번호", "시행일자", "개정요약", "개정상세", "상세링크", "최종수집일",
]


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


def _t(elem, path):
    found = elem.find(path)
    return (found.text or "").strip() if found is not None and found.text else ""


def _fmt_date(yyyymmdd):
    s = (yyyymmdd or "").strip()
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 else s


def classify_kind(name, kind_name):
    """"하도급법 시행령"처럼 이름 끝으로 시행령/시행규칙을 가른다. 일괄개정령
    ("…에 관한 대통령령")처럼 둘 다 아닌 건 법령구분명(대통령령/총리령)을 쓴다."""
    compact = name.replace(" ", "")
    if compact.endswith("시행령"):
        return "시행령"
    if compact.endswith("시행규칙"):
        return "시행규칙"
    return kind_name


def fetch_history(cutoff, max_pages=15):
    """공포일자 내림차순으로 훑다가 한 페이지가 통째로 cutoff 이전이면 멈춘다.
    MST별로 묶어 {mst: {...}}를 돌려준다."""
    by_mst = {}
    for page in range(1, max_pages + 1):
        params = {
            "OC": LAW_API_OC, "target": "eflaw", "type": "XML", "org": FTC_ORG_CODE,
            "nw": "1,2,3", "sort": "ddes", "display": 100, "page": page,
        }
        res = get_law_api_with_retry(f"{LAW_API_BASE}/lawSearch.do", params)
        root = ET.fromstring(res.content)
        items = root.findall(".//law")
        if not items:
            break
        for law in items:
            promulgated = _t(law, "공포일자")
            if promulgated < cutoff:
                continue
            name = _t(law, "법령명한글")
            if any(k in name for k in EXCLUDE_NAME_KEYWORDS):
                continue
            mst = _t(law, "법령일련번호")
            entry = by_mst.setdefault(mst, {
                "법령일련번호": mst,
                "법령ID": _t(law, "법령ID"),
                "법령명": name,
                "법령약칭": _t(law, "법령약칭명"),
                "법령종류": classify_kind(name, _t(law, "법령구분명")),
                "제개정구분": _t(law, "제개정구분명"),
                "공포일자": promulgated,
                "공포번호": _t(law, "공포번호"),
                "시행일자목록": set(),
            })
            entry["시행일자목록"].add(_t(law, "시행일자"))
        if max(_t(law, "공포일자") for law in items) < cutoff or len(items) < 100:
            break
        time.sleep(0.2)
    return by_mst


def _clean(text):
    lines = [ln.rstrip() for ln in (text or "").replace("\r", "").split("\n")]
    lines = [ln for ln in lines if ln.strip() and ln.strip() != "<법제처 제공>"]
    return "\n".join(lines)


def summarize_reason(reason):
    """제개정이유에서 "◇ 주요내용" 아래 "가. ○○" 같은 맨 윗단계 제목만 뽑는다.
    그런 목록이 없으면(일괄개정의 "개정이유 및 주요내용" 한 문단 등) 본문 첫
    문단을 쓴다."""
    lines = reason.split("\n")
    headlines = []
    in_main = False
    for ln in lines:
        s = ln.strip()
        if s.startswith("◇"):
            in_main = "주요내용" in s
            continue
        m = re.match(r"^([가-하])\.\s*(.+)$", s)
        if in_main and m:
            headlines.append(f"{m.group(1)}. {m.group(2)}")
    if headlines:
        return "\n".join(headlines)
    body = [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith(("◇", "["))]
    first = body[0] if body else ""
    return first if len(first) <= 200 else first[:200] + "…"


def other_law_excerpt(gaejeongmun, law_name):
    """타법개정의 개정문(다른 법령의 부칙 "다른 법령의 개정")에서 원인 법령명과,
    이 법령을 고친 문단만 잘라낸다. 못 찾으면 ("", "")."""
    source = ""
    m = re.search(r"⊙[^\n]*제\d+호[^\n]*\n\s*([^\n]+)", gaejeongmun)
    if m:
        source = m.group(1).strip()
    lines = gaejeongmun.split("\n")
    target = law_name.replace(" ", "")
    start = None
    for i, ln in enumerate(lines):
        if target in ln.replace(" ", "") and "다음과 같이 개정한다" in ln:
            start = i + 1
            break
    if start is None:
        return source, ""
    excerpt = []
    base_indent = None
    prev = ""
    for ln in lines[start:]:
        s = ln.strip()
        indent = len(ln) - len(ln.lstrip())
        if base_indent is None:
            base_indent = indent
        if s == "부칙":
            break
        # 지시문과 같은 깊이에서 다음 법령 번호(<72>, ②), 일괄개정령의 다음 조
        # ("제2조(…)"), "제3조부터 제136조까지 생략"이 나오면 끊는다. 고친 내용
        # 자체도 "제2조제5항 중 …"으로 시작하므로 "제N조" 뒤에 괄호나 "생략"이
        # 붙은 것만 경계로 보고, "제92조를 다음과 같이 한다." 바로 다음 줄(새
        # 조문 본문 "제92조(…)")과 그보다 깊이 들여쓴 줄은 본문으로 둔다.
        if (indent <= base_indent and "다음과 같이" not in prev
                and re.match(r"^(<\d+>|[①-⑳]|제\d+조(의\d+)?\(|제\d+조.*생략$)", s)):
            break
        excerpt.append(s)
        prev = s
    return source, "\n".join(x for x in excerpt if x)


def fetch_detail(mst, ef_date, law_name, revision_kind):
    """제개정이유/개정문을 받아 (개정요약, 개정상세)를 만든다."""
    params = {"OC": LAW_API_OC, "target": "eflaw", "MST": mst, "efYd": ef_date, "type": "XML"}
    res = get_law_api_with_retry(f"{LAW_API_BASE}/lawService.do", params, timeout=40)
    root = ET.fromstring(res.content)
    reason_el = root.find(".//제개정이유")
    reason = _clean("".join(reason_el.itertext())) if reason_el is not None else ""
    gm_el = root.find(".//개정문")
    gaejeongmun = _clean("".join(gm_el.itertext())) if gm_el is not None else ""

    if revision_kind == "타법개정":
        source, excerpt = other_law_excerpt(gaejeongmun, law_name)
        summary = f"「{source}」 제·개정에 따른 정비" if source else "다른 법령 제·개정에 따른 정비"
        return summary, excerpt
    # 원문에 이유가 없는 공포본도 있다 - 빈칸으로 두면 캐시가 안 걸려 매일 다시 조회한다.
    return summarize_reason(reason) or "(제개정이유 원문 없음)", reason


def load_cached_details():
    if not os.path.exists(SUBORDINATE_LAW_LIST_PATH) or os.path.getsize(SUBORDINATE_LAW_LIST_PATH) == 0:
        return {}
    try:
        old = pd.read_csv(SUBORDINATE_LAW_LIST_PATH, dtype=str, keep_default_na=False)
    except Exception:
        return {}
    return {
        row["법령일련번호"]: (row["개정요약"], row["개정상세"])
        for _, row in old.iterrows() if row.get("개정요약")
    }


def main():
    # 06:01 실행 때만 수집하고 같은 날 07:12/07:47/12:01/18:01은 건너뛴다.
    # 지금 당장 돌리려면 SUBORDINATE_LAWS_SKIP_WAIT=true(daily.yml 입력으로 넘어옴).
    skip_wait = os.environ.get("SUBORDINATE_LAWS_SKIP_WAIT", "").strip().lower() == "true"
    now = datetime.now(KST)
    if not skip_wait and now.hour != 6:
        print(f"[시행령·시행규칙 개정 수집] 하루 1번(06시대)만 실행하도록 정해둬서, "
              f"이번 실행({now.strftime('%a %H:%M')})은 건너뜁니다.")
        return

    if LAW_API_OC == "test":
        print("[경고] LAW_GO_KR_OC가 없어 데모키(test)로 호출합니다 - 정식 서비스에는 부적합합니다.")

    cutoff = (now - timedelta(days=LOOKBACK_DAYS)).strftime("%Y%m%d")
    try:
        by_mst = fetch_history(cutoff)
    except Exception:
        print(f"[시행령·시행규칙 목록 조회 실패] 기존 파일을 그대로 둡니다.\n{traceback.format_exc()}")
        return
    if not by_mst:
        print("[경고] 조회된 시행령·시행규칙 개정이 없습니다 - subordinate_law_list.csv를 덮어쓰지 않습니다.")
        return

    cached = load_cached_details()
    now_str = now.strftime("%Y-%m-%d %H:%M")
    rows = []
    fetched = 0
    for mst, e in by_mst.items():
        ef_dates = sorted(d for d in e.pop("시행일자목록") if d)
        summary, detail = cached.get(mst, ("", ""))
        if not summary and ef_dates:
            try:
                summary, detail = fetch_detail(mst, ef_dates[0], e["법령명"], e["제개정구분"])
                fetched += 1
            except Exception:
                print(f"[개정이유 조회 실패] {e['법령명']} (MST {mst}):\n{traceback.format_exc()}")
            time.sleep(0.2)
        rows.append({
            **e,
            "공포일자": _fmt_date(e["공포일자"]),
            "시행일자": ";".join(_fmt_date(d) for d in ef_dates),
            "개정요약": summary,
            "개정상세": detail,
            "상세링크": PUBLIC_LAW_LINK.format(mst=mst),
            "최종수집일": now_str,
        })

    df = pd.DataFrame(rows, columns=CSV_COLUMNS).sort_values(["공포일자", "법령명"], ascending=[False, True])
    df.to_csv(SUBORDINATE_LAW_LIST_PATH, index=False, encoding="utf-8-sig")
    print(f"[법령 공포·시행 이력 수집 완료] {len(df)}건 (개정이유 새로 조회 {fetched}건) -> {SUBORDINATE_LAW_LIST_PATH}")


if __name__ == "__main__":
    main()
