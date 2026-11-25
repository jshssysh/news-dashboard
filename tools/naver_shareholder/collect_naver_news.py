"""주요 인사 '주주보호·자본시장' 발언 기사 후보 수집 (네이버 뉴스 검색 API).

임시 도구다. 대시보드 수집(src/)과 관계없고, 결과는 저장소에 커밋하지 않는다.
- 수집: 인물 검색명 × 이슈 키워드 조합으로 최신순 검색, 시작일 이전 기사가 나오면 그 검색어는 멈춤
- 필터: 기간 → 인물(동명이인) → 관련성(이슈 키워드)
- 중복 제거: 원문링크 같음 → 같은 날짜·발언자 + 제목 유사도 0.85 이상
- 기존 엑셀('워딩 정리' 시트 G열) 대조, 자동 태깅, 엑셀 저장

API 키는 환경변수(또는 .env)의 NAVER_CLIENT_ID / NAVER_CLIENT_SECRET에서만 읽는다.
키 값은 어디에도 출력하지 않는다.

사용 예:
  python collect_naver_news.py --start 2026-01-01 --end 2026-11-25
  python collect_naver_news.py --dry-run
  python collect_naver_news.py --limit-queries 3
  python collect_naver_news.py --compare-only naver_후보_261125.xlsx --existing 261125_주요인사_주주보호_자본시장_워딩_정리.xlsx
"""

import argparse
import difflib
import html
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse

import requests
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill

try:
    from dotenv import load_dotenv
except ImportError:  # Actions에서는 환경변수로 바로 받으므로 없어도 된다
    load_dotenv = None

KST = timezone(timedelta(hours=9))

# ---------------------------------------------------------------------------
# 수집 조건 (지시서 4장)
# ---------------------------------------------------------------------------
# (발언자 표기, 검색명, 동명이인 필터 단어 - 하나 이상 있어야 통과)
PERSONS = [
    ("대통령", "이재명 대통령", ["대통령", "李"]),
    ("금융위원장", "이억원", ["금융위"]),
    ("금감원장", "이찬진", ["금감원", "금융감독원"]),
    ("김민석 대표", "김민석", ["대표", "총리", "민주당"]),
    ("김현정 의원", "김현정 의원", ["의원", "민주당"]),
    ("오기형 의원", "오기형", ["의원", "특위"]),
    ("김남근 의원", "김남근", ["의원", "특위"]),
]
PERSON_ORDER = {p[0]: i for i, p in enumerate(PERSONS)}

ISSUE_KEYWORDS = [
    "의무공개매수", "중복상장", "쪼개기 상장", "자사주 소각", "주가누르기", "주가 누르기", "상속세 주가",
    "적대적 M&A", "상장폐지", "상장유지", "IR 의무화", "합병가액", "베어허그", "5%룰", "스튜어드십",
    "주주환원", "주가조작", "불공정거래", "저PBR", "일반주주", "소액주주", "주주보호", "주주충실의무",
    "상법 개정", "코리아 디스카운트", "자본시장",
]

# 자동 태깅 규칙 (지시서 6장, 위에 있을수록 우선)
TAG_RULES = [
    ("주가누르기 방지법(상증세법)", "상", ["주가누르기", "주가 누르기", "상속세", "상증세", "증여세"]),
    ("의무공개매수제", "상", ["의무공개매수", "안분비례"]),
    ("중복상장 원칙금지", "상", ["중복상장", "쪼개기 상장", "물적분할", "자회사 상장"]),
    ("합병가액 공정화", "상", ["합병가액", "합병비율", "공정가액", "외부평가"]),
    ("적대적 M&A 활성화(방어장치 전수조사)", "상", ["적대적 M&A", "경영권 방어", "포이즌필"]),
    ("상장유지 요건-대주주 지분율", "상", ["상장유지", "유통주식", "대주주 지분율"]),
    ("자사주 소각 의무화(3차 상법)", "중", ["자사주", "3차 상법"]),
    ("저PBR 기업 명단 공표", "중", ["저PBR", "PBR 하위", "명단 공표"]),
    ("한국형 베어허그", "중", ["베어허그"]),
    ("5%룰·스튜어드십 코드", "중", ["5%룰", "스튜어드십"]),
    ("공시심사·불공정거래 단속 강화", "중",
     ["주가조작", "불공정거래", "시세조종", "패가망신", "중수청", "특사경", "증권신고서"]),
    ("주주환원 확대 압박", "하", ["주주환원", "배당"]),
    ("IR 의무화·영문공시", "하", ["IR", "기업설명회", "영문공시"]),
    ("상장폐지 요건 강화(코스닥 중심)", "하", ["상장폐지", "동전주", "코스닥 퇴출"]),
    ("소수주주 주주제안권", "하", ["주주제안"]),
]
IMPACT_ORDER = {"상": 0, "중": 1, "하": 2, "-": 3}

API_URLS = {
    "developers": "https://openapi.naver.com/v1/search/news.json",
}
DISPLAY = 100
MAX_START = 1000          # start + display - 1 <= 1000
CALL_INTERVAL = 0.15      # 호출 간격(초)
MAX_RETRY = 3
SIMILARITY = 0.85
EXISTING_FILE = "261125_주요인사_주주보호_자본시장_워딩_정리.xlsx"
EXISTING_SHEET = "워딩 정리"

COLUMNS = ["No", "날짜", "발언자", "검색어", "제목", "요약(description)", "언론사 도메인", "원문링크",
           "네이버링크", "이슈", "영향 항목(추정)", "영향도(추정)", "동일보도_건수", "기존반영", "검토"]
COL_WIDTHS = [6, 11, 12, 24, 50, 70, 18, 40, 40, 22, 30, 10, 10, 8, 20]


class AuthError(Exception):
    pass


# ---------------------------------------------------------------------------
# 문자열 처리
# ---------------------------------------------------------------------------
TAG_RE = re.compile(r"<[^>]+>")
URL_RE = re.compile(r"https?://[^\s\"'<>)\]]+")


def clean(text):
    return html.unescape(TAG_RE.sub("", text or "")).strip()


def _kw_pattern(kw):
    """영문이 들어간 키워드(IR 등)는 영단어 안에 섞인 경우(FAIR 등)를 빼고 찾는다."""
    esc = re.escape(kw)
    if re.search(r"[A-Za-z]", kw):
        return re.compile(r"(?<![A-Za-z])" + esc + r"(?![A-Za-z])", re.IGNORECASE)
    return re.compile(esc)


_PATTERNS = {}


def has_kw(text, kw):
    """키워드가 text에 있는지. 띄어쓰기 차이('쪼개기상장')도 잡는다."""
    if kw not in _PATTERNS:
        _PATTERNS[kw] = (_kw_pattern(kw), _kw_pattern(kw.replace(" ", "")))
    p, p_nospace = _PATTERNS[kw]
    return bool(p.search(text) or p_nospace.search(text.replace(" ", "")))


def norm_url(u):
    u = (u or "").strip()
    if not u:
        return ""
    u = re.sub(r"^https?://", "", u, flags=re.IGNORECASE)
    u = re.sub(r"^(www\.|m\.)", "", u, flags=re.IGNORECASE)
    return u.rstrip("/").lower()


def domain_of(url):
    host = urlparse(url or "").netloc.lower()
    return host[4:] if host.startswith("www.") else host


def parse_date(s):
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=KST)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
def load_keys():
    if load_dotenv:
        load_dotenv(Path(__file__).with_name(".env"))
        load_dotenv()
    mode = (os.environ.get("NAVER_API_MODE") or "developers").strip().lower()
    cid = (os.environ.get("NAVER_CLIENT_ID") or "").strip().strip("'\"")
    secret = (os.environ.get("NAVER_CLIENT_SECRET") or "").strip().strip("'\"")
    if mode not in API_URLS:
        sys.exit(f"[오류] NAVER_API_MODE='{mode}'는 아직 지원하지 않습니다. "
                 "apihub 뉴스 경로를 공식 문서에서 확인하기 전까지는 developers만 씁니다.")
    if not cid or not secret:
        sys.exit("[오류] NAVER_CLIENT_ID 또는 NAVER_CLIENT_SECRET이 없습니다. "
                 ".env 파일이나 환경변수(Actions는 저장소 Secrets)를 확인하세요.")
    return mode, {"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": secret}


def call_api(session, url, headers, params, stats):
    """한 페이지 호출. 성공하면 dict, 건너뛸 오류면 (None, 메시지). 인증 실패는 AuthError."""
    last = ""
    for attempt in range(MAX_RETRY + 1):
        time.sleep(CALL_INTERVAL)
        stats["calls"] += 1
        try:
            r = session.get(url, headers=headers, params=params, timeout=15)
        except requests.RequestException as e:
            last = f"네트워크 오류: {type(e).__name__}"
        else:
            if r.status_code == 200:
                return r.json(), None
            code = _error_code(r)
            if r.status_code in (401, 403):
                raise AuthError(f"HTTP {r.status_code} {code}".strip())
            if r.status_code == 400:
                return None, f"HTTP 400 {code}".strip()
            last = f"HTTP {r.status_code} {code}".strip()
            if not (r.status_code == 429 or r.status_code >= 500):
                return None, last
        if attempt < MAX_RETRY:
            time.sleep(2 ** attempt)
    return None, f"{last} (재시도 {MAX_RETRY}회 실패)"


def _error_code(r):
    try:
        j = r.json()
        return f"{j.get('errorCode', '')} {j.get('errorMessage', '')}".strip()
    except ValueError:
        return ""


def fetch_query(session, url, headers, query, start_dt, stats):
    """한 검색어를 최신순으로 받는다. 시작일 이전 기사가 나오면 멈춘다."""
    items, errors, calls_before = [], [], stats["calls"]
    start = 1
    while start + DISPLAY - 1 <= MAX_START:
        data, err = call_api(session, url, headers,
                             {"query": query, "display": DISPLAY, "start": start, "sort": "date"}, stats)
        if err:
            errors.append(f"start={start}: {err}")
            break
        page = data.get("items", [])
        items.extend(page)
        if len(page) < DISPLAY:
            break
        try:
            oldest = parsedate_to_datetime(page[-1]["pubDate"])
        except (TypeError, ValueError, KeyError):
            oldest = None
        if oldest and oldest < start_dt:
            break
        start += DISPLAY
    return items, errors, stats["calls"] - calls_before


# ---------------------------------------------------------------------------
# 처리
# ---------------------------------------------------------------------------
def build_queries():
    return [(p, f"{p[1]} {kw}") for p in PERSONS for kw in ISSUE_KEYWORDS]


def tag(text):
    for item, impact, kws in TAG_RULES:
        if any(has_kw(text, k) for k in kws):
            return item, impact
    return "(해당 없음)", "-"


def process_items(raw, person, query, start_dt, end_dt, log):
    speaker, _, filter_words = person
    out = []
    for it in raw:
        title, desc = clean(it.get("title")), clean(it.get("description"))
        try:
            pub = parsedate_to_datetime(it.get("pubDate", "")).astimezone(KST)
        except (TypeError, ValueError):
            log["기간 제외"] += 1
            continue
        if not (start_dt <= pub < end_dt):
            log["기간 제외"] += 1
            continue
        text = f"{title} {desc}"
        if not any(w in text for w in filter_words):
            log["인물 제외"] += 1
            continue
        issues = [k for k in ISSUE_KEYWORDS if has_kw(text, k)]
        if not issues:
            log["관련성 제외"] += 1
            continue
        item, impact = tag(text)
        orig = (it.get("originallink") or "").strip()
        link = (it.get("link") or "").strip()
        out.append({
            "pub": pub, "날짜": pub.strftime("%Y.%m.%d"), "발언자": speaker, "검색어": [query],
            "제목": title, "요약(description)": desc, "언론사 도메인": domain_of(orig or link),
            "원문링크": orig, "네이버링크": link, "이슈": ", ".join(issues),
            "영향 항목(추정)": item, "영향도(추정)": impact, "동일보도_건수": 1, "기존반영": "", "검토": "",
        })
    log["통과"] = len(out)
    return out


def dedup(rows, summary):
    # 1차: 원문링크(없으면 네이버링크) 기준. 먼저 나온 행(인물 순서가 앞선 쪽)을 남기고 검색어를 합친다.
    by_url, first = {}, []
    for r in rows:
        key = norm_url(r["원문링크"] or r["네이버링크"])
        if key in by_url:
            kept = by_url[key]
            for q in r["검색어"]:
                if q not in kept["검색어"]:
                    kept["검색어"].append(q)
            continue
        by_url[key] = r
        first.append(r)
    summary["1차 중복 제거(링크)"] = len(rows) - len(first)

    # 2차: 같은 날짜·같은 발언자 + 제목 유사도
    groups = {}
    for r in first:
        groups.setdefault((r["날짜"], r["발언자"]), []).append(r)
    result = []
    for members in groups.values():
        reps = []
        for r in members:
            t = re.sub(r"\s+", "", r["제목"])
            for rep in reps:
                if difflib.SequenceMatcher(None, rep["_t"], t).ratio() >= SIMILARITY:
                    rep["동일보도_건수"] += 1
                    break
            else:
                r["_t"] = t
                reps.append(r)
        result.extend(reps)
    summary["2차 중복 제거(제목 유사)"] = len(first) - len(result)
    for r in result:
        r.pop("_t", None)
    return result


def load_existing_urls(path):
    """기존 엑셀 '워딩 정리' 시트 G열의 URL(값·하이퍼링크)을 모은다. 파일은 읽기만 한다."""
    wb = load_workbook(path, read_only=False, data_only=True)
    if EXISTING_SHEET not in wb.sheetnames:
        print(f"[경고] 기존 파일에 '{EXISTING_SHEET}' 시트가 없어 대조를 건너뜁니다.")
        return set()
    ws = wb[EXISTING_SHEET]
    urls = set()
    for row in ws.iter_rows(min_col=7, max_col=7):
        c = row[0]
        for s in (str(c.value or ""), c.hyperlink.target if c.hyperlink else ""):
            for u in URL_RE.findall(s or ""):
                urls.add(norm_url(u))
    wb.close()
    return urls


def find_existing(arg):
    if arg:
        p = Path(arg)
        if not p.exists():
            sys.exit(f"[오류] 기존 파일을 찾을 수 없습니다: {p}")
        return p
    for d in (Path.cwd(), Path(__file__).parent):
        if (d / EXISTING_FILE).exists():
            return d / EXISTING_FILE
    return None


def mark_existing(rows, urls):
    n = 0
    for r in rows:
        if norm_url(r["원문링크"]) in urls or norm_url(r["네이버링크"]) in urls:
            r["기존반영"] = "Y"
            n += 1
    return n


# ---------------------------------------------------------------------------
# 엑셀
# ---------------------------------------------------------------------------
FONT = "맑은 고딕"
HEAD_FILL = PatternFill("solid", fgColor="1F3864")


def style_header(ws, widths):
    for i, c in enumerate(ws[1], start=1):
        c.font = Font(name=FONT, bold=True, color="FFFFFF")
        c.fill = HEAD_FILL
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.column_dimensions[c.column_letter].width = widths[i - 1] if i <= len(widths) else 15
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions


def save_excel(path, rows, query_logs, summary):
    wb = Workbook()
    ws = wb.active
    ws.title = "후보목록"
    ws.append(COLUMNS)
    wrap_cols = {COLUMNS.index("제목") + 1, COLUMNS.index("요약(description)") + 1}
    for no, r in enumerate(rows, start=1):
        vals = [no] + [("\n".join(r[k]) if k == "검색어" else r[k]) for k in COLUMNS[1:]]
        ws.append(vals)
        for ci, c in enumerate(ws[ws.max_row], start=1):
            c.font = Font(name=FONT)
            c.alignment = Alignment(vertical="top", wrap_text=ci in wrap_cols or ci == 4)
        for key in ("원문링크", "네이버링크"):
            c = ws.cell(row=ws.max_row, column=COLUMNS.index(key) + 1)
            if c.value:
                c.hyperlink = c.value
                c.font = Font(name=FONT, color="0563C1", underline="single")
    style_header(ws, COL_WIDTHS)

    log_cols = ["검색어", "발언자", "호출 수", "받은 건수", "기간 제외", "인물 제외", "관련성 제외", "통과", "오류"]
    ws2 = wb.create_sheet("수집로그")
    ws2.append(log_cols)
    for lg in query_logs:
        ws2.append([lg.get(k, "") if k != "오류" else " / ".join(lg["오류"]) for k in log_cols])
    style_header(ws2, [26, 12, 8, 10, 10, 10, 11, 8, 50])
    ws2.append([])
    for k, v in summary.items():
        ws2.append([k, v])
    for row in ws2.iter_rows(min_row=2):
        for c in row:
            c.font = Font(name=FONT, bold=c.font.bold)
    wb.save(path)


def compare_only(cand_path, existing_arg):
    """이미 받은 후보 파일에 기존반영만 채워 새 파일로 저장한다(로컬용, API 호출 없음)."""
    existing = find_existing(existing_arg)
    if not existing:
        sys.exit(f"[오류] 기존 파일이 없습니다. --existing으로 '{EXISTING_FILE}' 경로를 지정하세요.")
    urls = load_existing_urls(existing)
    wb = load_workbook(cand_path)
    ws = wb["후보목록"]
    head = [c.value for c in ws[1]]
    ci = {h: i for i, h in enumerate(head)}
    n = total = 0
    for row in ws.iter_rows(min_row=2):
        total += 1
        o, l = row[ci["원문링크"]].value, row[ci["네이버링크"]].value
        hit = norm_url(o) in urls or norm_url(l) in urls
        row[ci["기존반영"]].value = "Y" if hit else ""
        n += hit
    out = Path(cand_path)
    out = out.with_name(out.stem + "_대조" + out.suffix)
    v = 2
    while out.exists():
        out = Path(cand_path).with_name(f"{Path(cand_path).stem}_대조_v{v}.xlsx")
        v += 1
    wb.save(out)
    print(f"기존 파일 URL {len(urls)}개와 대조: 후보 {total}건 중 기존반영 Y {n}건")
    print(f"저장: {out}")


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="네이버 뉴스 API로 주요 인사 주주보호·자본시장 발언 기사 후보 수집")
    today = datetime.now(KST).strftime("%Y-%m-%d")
    ap.add_argument("--start", default="2026-01-01", help="시작일 YYYY-MM-DD (KST, 기본 2026-01-01)")
    ap.add_argument("--end", default=today, help="종료일 YYYY-MM-DD (KST, 그날 포함, 기본 오늘)")
    ap.add_argument("--dry-run", action="store_true", help="검색어 목록과 예상 최대 호출 수만 출력")
    ap.add_argument("--limit-queries", type=int, default=0, help="테스트용: 앞에서부터 검색어 N개만 실행")
    ap.add_argument("--existing", help=f"기존 엑셀 경로(기본: 현재 폴더의 {EXISTING_FILE})")
    ap.add_argument("--out-dir", default=".", help="결과 파일을 둘 폴더")
    ap.add_argument("--compare-only", metavar="후보파일", help="받은 후보 파일에 기존반영만 채움(API 호출 없음)")
    args = ap.parse_args()

    if args.compare_only:
        compare_only(args.compare_only, args.existing)
        return

    start_dt = parse_date(args.start)
    end_dt = parse_date(args.end) + timedelta(days=1)
    queries = build_queries()
    if args.limit_queries > 0:
        queries = queries[:args.limit_queries]
    max_calls = len(queries) * (MAX_START // DISPLAY)

    if args.dry_run:
        for i, (_, q) in enumerate(queries, start=1):
            print(f"{i:3d}. {q}")
        print(f"\n검색어 {len(queries)}개, 예상 최대 호출 수 {max_calls}회 "
              f"(검색어당 최대 {MAX_START // DISPLAY}페이지, 재시도 제외)")
        print(f"기간: {args.start} ~ {args.end} (KST)")
        return

    mode, headers = load_keys()
    url = API_URLS[mode]
    run_at = datetime.now(KST)
    print(f"수집 시작: 검색어 {len(queries)}개, 기간 {args.start} ~ {args.end}, 모드 {mode}")

    stats = {"calls": 0}
    rows, query_logs = [], []
    session = requests.Session()
    try:
        for i, (person, q) in enumerate(queries, start=1):
            raw, errors, calls = fetch_query(session, url, headers, q, start_dt, stats)
            log = {"검색어": q, "발언자": person[0], "호출 수": calls, "받은 건수": len(raw),
                   "기간 제외": 0, "인물 제외": 0, "관련성 제외": 0, "통과": 0, "오류": errors}
            rows.extend(process_items(raw, person, q, start_dt, end_dt, log))
            query_logs.append(log)
            print(f"[{i}/{len(queries)}] {q}: 받음 {len(raw)}, 통과 {log['통과']}"
                  + (f", 오류 {len(errors)}" if errors else ""))
    except AuthError as e:
        sys.exit(f"[오류] 인증 실패({e}). NAVER_CLIENT_ID/SECRET 값과 개발자센터의 "
                 "'검색' API 사용 설정을 확인하세요. (호출 {0}회에서 중단)".format(stats["calls"]))

    summary = {"실행 일시": run_at.strftime("%Y-%m-%d %H:%M KST"),
               "기간": f"{args.start} ~ {args.end}", "검색어 수": len(queries),
               "총 호출 수": stats["calls"], "필터 통과(중복 제거 전)": len(rows)}
    rows = dedup(rows, summary)

    existing = find_existing(args.existing)
    if existing:
        n_exist = mark_existing(rows, load_existing_urls(existing))
        summary["기존 파일 대조"] = existing.name
    else:
        n_exist = 0
        summary["기존 파일 대조"] = "파일 없음(대조 안 함)"

    rows.sort(key=lambda r: (r["pub"].date(), PERSON_ORDER[r["발언자"]], IMPACT_ORDER[r["영향도(추정)"]]))
    by_impact = {k: sum(r["영향도(추정)"] == k for r in rows) for k in IMPACT_ORDER}
    summary["최종 후보 수"] = len(rows)
    for k, v in by_impact.items():
        summary[f"영향도 {k}"] = v
    summary["기존반영 Y"] = n_exist
    summary["오류 난 검색어 수"] = sum(1 for lg in query_logs if lg["오류"])

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"naver_후보_{run_at.strftime('%y%m%d')}.xlsx"
    save_excel(out, rows, query_logs, summary)

    print("\n===== 요약 =====")
    print(f"총 호출 수: {stats['calls']}")
    print(f"최종 후보 수: {len(rows)} (링크 중복 {summary['1차 중복 제거(링크)']}건, "
          f"제목 유사 {summary['2차 중복 제거(제목 유사)']}건 묶음)")
    print("영향도별: " + ", ".join(f"{k} {v}" for k, v in by_impact.items()))
    print(f"기존반영(Y): {n_exist}" + ("" if existing else " (기존 파일 없음, 대조 안 함)"))
    if summary["오류 난 검색어 수"]:
        print(f"오류 난 검색어: {summary['오류 난 검색어 수']}개 (수집로그 시트 참고)")
    print(f"저장: {out}")


if __name__ == "__main__":
    main()
