"""
법제처 국민참여입법센터(opinion.lawmaking.go.kr)의 정부 입법예고 중 지금 챙기는
부처·법령(공정위 소관 전체 + collect_subordinate_laws.OTHER_MINISTRY_LAWS) 것만
모아 legislative_notice_list.csv로 저장한다. 입법 탭 단계 줄의 "입법예고" 칩이
이 파일을 보여준다(2026-10-27 사용자 요청).

각 부처 홈페이지의 입법예고도 결국 이곳에 같이 올라온다. 정식 Open API
(/rest/ogLmPp.xml)는 따로 받은 인증키가 있어야 해서(법제처 국가법령정보 OC로는
401), 누구나 여는 목록·상세 화면을 읽는다(2026-10-27 실측):
- 목록 /gcom/ogLmPp?pageIndex=N&finishIncludeYn=Y: 한 페이지 20건, 등록 순(최신 먼저).
  finishIncludeYn=Y를 빼면 의견 접수 중인 것만 온다. 행마다 제목·제개정구분·
  소관부처(법령종류)·접수기간이 있다.
- 상세 /gcom/ogLmPp/<예고ID>: 공고문 본문("1. 개정이유 / 2. 주요내용 / 3. 의견제출").
  본문은 바뀌지 않으므로 한 번 받은 예고ID는 기존 CSV 값을 재사용한다.

예고 시작일이 최근 LOOKBACK_DAYS일 안인 것만 훑고, 이미 받은 행은 마감 뒤
KEEP_DAYS일까지 남겨 둔다(목록에서 늦게 밀려나도 사라지지 않게).

실행: python collect_legislative_notices.py
"""
import html
import os
import re
import time
import traceback
from datetime import datetime, timedelta, timezone

import requests
import pandas as pd

from collect_subordinate_laws import EXCLUDE_NAME_KEYWORDS, OTHER_MINISTRY_LAWS, _name_key

KST = timezone(timedelta(hours=9))
NOTICE_LIST_PATH = "legislative_notice_list.csv"
BASE = "https://opinion.lawmaking.go.kr"
LIST_URL = f"{BASE}/gcom/ogLmPp"
DETAIL_URL = BASE + "/gcom/ogLmPp/{nid}"
FTC_NAME = "공정거래위원회"
LOOKBACK_DAYS = 90
KEEP_DAYS = 180
MAX_PAGES = 60
HEADERS = {"User-Agent": "Mozilla/5.0 (news-dashboard; legislative notice collector)"}

CSV_COLUMNS = [
    "예고ID", "법령명", "예고제목", "제개정구분", "소관부처", "담당부서", "법령종류",
    "예고시작일", "예고종료일", "개정요약", "개정상세", "상세링크", "최종수집일",
]

# 제목 끝의 "일부개정령안 입법예고" 같은 꼬리를 떼면 법령명이 남는다.
_TITLE_TAIL = re.compile(r"\s*(?:\(?(?:일부|전부)?개정|제정|폐지)(?:법률|령|규칙)?\s*\(?안\)?.*$")
# 법령명 키 뒤에 이런 말이 이어지면 같은 법의 법률·시행령·시행규칙 개정안이다
# ("상법" 키가 "상표법"이 아니라 "상법 시행령 일부개정령안"에만 맞게).
_AFTER_KEY = ("시행령", "시행규칙", "일부", "전부", "제정", "폐지", "개정", "(")
WATCH_KEYS = [_name_key(n) for n in OTHER_MINISTRY_LAWS]


def get_with_retry(url, params=None, timeout=30, retries=2, retry_wait=10):
    last_exc = None
    for attempt in range(retries + 1):
        try:
            res = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
            if (res.status_code >= 500 or not res.content.strip()) and attempt < retries:
                time.sleep(retry_wait)
                continue
            res.raise_for_status()
            return res
        except requests.exceptions.RequestException as e:
            last_exc = e
            if attempt < retries:
                time.sleep(retry_wait)
                continue
            raise
    raise last_exc


def _text(fragment):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment or ""))).strip()


def _date(s):
    m = re.search(r"(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})", s or "")
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else ""


def law_name_of(title):
    return _TITLE_TAIL.sub("", re.sub(r"\s*입법예고\s*$", "", title)).strip()


def is_watched(ministry, law_name):
    # 공정위 직제처럼 내부 조직 개편은 법령 공포·시행 목록과 같이 뺀다.
    if any(k in law_name for k in EXCLUDE_NAME_KEYWORDS):
        return False
    if FTC_NAME in ministry:
        return True
    key = _name_key(law_name)
    for k in WATCH_KEYS:
        if key == k or (key.startswith(k) and key[len(k):].startswith(_AFTER_KEY)):
            return True
    return False


def parse_list(page_html):
    rows = []
    for tr in re.findall(r"<tr>(.*?)</tr>", page_html, flags=re.S):
        link = re.search(r'href="/gcom/ogLmPp/(\d+)[^"]*"[^>]*>(.*?)</a>', tr, flags=re.S)
        if not link:
            continue
        cell = lambda th: (re.search(r'data-th="' + re.escape(th) + r'"[^>]*>(.*?)</td>', tr, flags=re.S) or [None, ""])[1]
        org = re.findall(r"<p>(.*?)</p>", cell("소관부처 (법령종류)"), flags=re.S)
        period = re.findall(r"<p>(.*?)</p>", cell("입법의견 접수기간"), flags=re.S)
        kind = re.search(r'class="ogmark_\d+"[^>]*>(.*?)</span>', tr, flags=re.S)
        rows.append({
            "예고ID": link.group(1),
            "예고제목": _text(link.group(2)),
            "제개정구분": _text(kind.group(1)) if kind else "",
            "소관부처": _text(org[0]) if org else "",
            "법령종류": _text(org[1]).strip("()") if len(org) > 1 else "",
            "예고시작일": _date(period[0]) if period else "",
            "예고종료일": _date(period[1]) if len(period) > 1 else "",
        })
    return rows


def fetch_list(cutoff):
    found = []
    for page in range(1, MAX_PAGES + 1):
        res = get_with_retry(LIST_URL, {"pageIndex": page, "finishIncludeYn": "Y", "chkFinishIncludeYn": "Y"})
        rows = parse_list(res.text)
        if not rows:
            break
        found.extend(r for r in rows if r["예고시작일"] >= cutoff)
        # 등록 순이라 시작일이 조금씩 뒤섞인다 - 한 페이지가 통째로 cutoff 이전일 때 멈춘다.
        if max(r["예고시작일"] for r in rows) < cutoff:
            break
        time.sleep(0.3)
    return found


def summarize(body):
    """공고문에서 "주요내용" 아래 가. 나. 다. 제목만 뽑는다(없으면 개정이유 첫 문장)."""
    m = re.search(r"주요\s*내용\s*\n(.*)", body, flags=re.S)
    if m:
        items = re.findall(r"^\s*([가-하])\.\s*(.+)$", m.group(1), flags=re.M)
        if items:
            return "\n".join(f"{k}. {v.strip()}" for k, v in items[:8])
    # "1. 개정이유 및 주요내용"처럼 한 덩어리면 제목 다음 첫 문단을 쓴다.
    lines = [x.strip() for x in body.split("\n")[1:] if x.strip()]
    return lines[0][:300] if lines else ""


def fetch_detail(nid):
    """(담당부서, 개정요약, 개정상세)를 돌려준다. 개정상세는 "개정이유"부터 "의견제출" 앞까지."""
    res = get_with_retry(DETAIL_URL.format(nid=nid))
    page = re.sub(r"<script.*?</script>|<style.*?</style>", "", res.text, flags=re.S)
    text = html.unescape(re.sub(r"<[^>]+>", "\n", page))
    text = re.sub(r"[ \t　]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    dept = re.search(r"소관부처\s*\n[^\n]*\n\s*\(\s*\n?\s*([^\n)]+?)\s*\n?\s*\)", text)
    start = re.search(r"\n\s*1\.\s*(?:개정|제정|폐지)?\s*이유", text)
    end = re.search(r"\n\s*\d\.\s*의견\s*제출", text[start.start():]) if start else None
    body = text[start.start(): start.start() + end.start()].strip() if start and end else ""
    return (dept.group(1).strip() if dept else ""), summarize(body), body[:6000]


def load_existing():
    if not os.path.exists(NOTICE_LIST_PATH) or os.path.getsize(NOTICE_LIST_PATH) == 0:
        return {}
    try:
        old = pd.read_csv(NOTICE_LIST_PATH, dtype=str, keep_default_na=False)
    except Exception:
        return {}
    return {row["예고ID"]: row.to_dict() for _, row in old.iterrows()}


def main():
    # collect_subordinate_laws.py와 같은 입력(SUBORDINATE_LAWS_SKIP_WAIT)으로 06시대에만 돈다.
    skip_wait = os.environ.get("SUBORDINATE_LAWS_SKIP_WAIT", "").strip().lower() == "true"
    now = datetime.now(KST)
    if not skip_wait and now.hour != 6:
        print(f"[입법예고 수집] 하루 1번(06시대)만 실행하도록 정해둬서, "
              f"이번 실행({now.strftime('%a %H:%M')})은 건너뜁니다.")
        return

    today = now.strftime("%Y-%m-%d")
    cutoff = (now - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    try:
        listed = fetch_list(cutoff)
    except Exception:
        print(f"[입법예고 목록 조회 실패] 기존 파일을 그대로 둡니다.\n{traceback.format_exc()}")
        return
    if not listed:
        print("[경고] 조회된 입법예고가 없습니다 - legislative_notice_list.csv를 덮어쓰지 않습니다.")
        return

    existing = load_existing()
    now_str = now.strftime("%Y-%m-%d %H:%M")
    merged = dict(existing)
    fetched = 0
    for r in listed:
        r["법령명"] = law_name_of(r["예고제목"])
        if not is_watched(r["소관부처"], r["법령명"]):
            continue
        old = existing.get(r["예고ID"], {})
        dept, summary, detail = old.get("담당부서", ""), old.get("개정요약", ""), old.get("개정상세", "")
        if not detail:
            try:
                dept, summary, detail = fetch_detail(r["예고ID"])
                fetched += 1
            except Exception:
                print(f"[입법예고 본문 조회 실패] {r['예고제목']} ({r['예고ID']}):\n{traceback.format_exc()}")
            time.sleep(0.3)
        merged[r["예고ID"]] = {
            **r, "담당부서": dept, "개정요약": summary, "개정상세": detail,
            "상세링크": DETAIL_URL.format(nid=r["예고ID"]), "최종수집일": now_str,
        }

    keep_from = (now - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    rows = [r for r in merged.values() if (r.get("예고종료일") or today) >= keep_from]
    df = pd.DataFrame(rows, columns=CSV_COLUMNS).sort_values(["예고시작일", "예고ID"], ascending=[False, False])
    df.to_csv(NOTICE_LIST_PATH, index=False, encoding="utf-8-sig")
    print(f"[입법예고 수집 완료] 목록 {len(listed)}건 중 챙기는 부처·법령 {len(df)}건 "
          f"(본문 새로 조회 {fetched}건) -> {NOTICE_LIST_PATH}")


if __name__ == "__main__":
    main()
