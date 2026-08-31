"""
news_list.csv + bill_list.csv + config/personnel.csv 를 읽어서 GitHub Pages에 올릴
정적 파일 세 장을 만든다.

  docs/index.html   화면 코드 (약 80KB) - 열자마자 골격이 뜬다
  docs/news.json    뉴스 데이터   - 페이지가 열리면서 바로 받는다
  docs/bills.json   법안 데이터   - 입법 탭을 볼 때만 받는다

예전에는 데이터를 전부 index.html 안에 박아 넣었는데, 그러다 보니 파일이 16MB가
됐다. 그 크기에서는 GitHub Pages(CDN)가 gzip 압축을 아예 안 해줘서 매번 16MB를
그대로 내려받게 됐다(실측: Content-Encoding 없음). 파일을 쪼개면
  - 첫 화면에 법안 9.8MB를 안 받고,
  - 파일이 작아져 CDN 압축이 걸릴 여지가 생기고,
  - 안 바뀐 파일은 브라우저 캐시(304)로 끝난다.

이 파일은 GitHub Actions(서버) 안에서만 돌고 브라우저에서는 순수 HTML/CSS/JS로만
동작하므로, 별도 서버나 런타임이 필요 없다(예전에는 Streamlit 앱을 같이 운영했지만
같은 화면을 두 벌 유지하는 부담 때문에 정적 대시보드로 일원화했다).
"""
import json
import os
from datetime import datetime, timedelta, timezone

import pandas as pd

KST = timezone(timedelta(hours=9))

# 중요 키워드 반복/과징금 금액 추출은 화면(html_template.html)에서 자바스크립트로
# 처리하므로 여기선 쓰지 않는다. 예전에 파이썬 쪽에도 같은 로직이 있었지만 호출되지
# 않는 죽은 코드였어서 제거함.
HIDDEN_CATEGORIES = ["삼성그룹", "삼성물산", "공정위인사"]

OUT_DIR = "docs"
OUT_PATH = os.path.join(OUT_DIR, "index.html")
NEWS_JSON_PATH = os.path.join(OUT_DIR, "news.json")
BILLS_JSON_PATH = os.path.join(OUT_DIR, "bills.json")
MEMBERS_JSON_PATH = os.path.join(OUT_DIR, "members.json")
WARNINGS_JSON_PATH = os.path.join(OUT_DIR, "warnings.json")
TEMPLATE_PATH = "html_template.html"

# 법안 상세링크는 의안ID만 갈아 끼운 같은 주소라, 데이터에 담지 않고 화면에서 만든다.
BILL_LINK_PREFIX = "https://likms.assembly.go.kr/bill/billDetail.do?billId="


def nz(value, default=None):
    """CSV의 빈 칸은 pandas에서 NaN이 되는데, json.dumps는 이걸 그대로 `NaN`으로
    적는다. JSON 규격에는 없는 값이라 fetch().json() 이 통째로 실패한다
    (예전처럼 HTML 안에 박아 넣을 때는 자바스크립트 리터럴이라 그냥 통과했다).

    화면 쪽 실제 피해도 있었다. 대표이슈가 NaN이면 이슈 묶기에서 객체 키가
    문자열 "NaN"이 되면서, 서로 아무 관계 없는 기사들이 한 이슈로 뭉쳤다
    (2026-08-18 분석 실패분 192건이 그렇게 한 덩어리가 됐다)."""
    return default if pd.isna(value) else value

# 화면 필터의 최대 옵션이 "최근 30일"이므로, 그보다 넉넉하게 최근 N일치만 정적 HTML에 담는다.
# news_list.csv 자체는 계속 쌓이지만(현재 7MB+), 그 전체를 매번 페이지에 박아넣으면
# 로딩이 느려지고 30KB 같은 작은 제한과 무관하게도 파일이 비대해진다.
RECENT_DAYS_WINDOW = 35


def load_data():
    try:
        df = pd.read_csv("news_list.csv")
    except Exception:
        return pd.DataFrame()
    # format을 명시하지 않으면, 옛 데이터의 발행일시="" 같은 불규칙한 값 때문에 pandas가
    # 빠른 벡터 파싱 대신 행 단위 dateutil 파싱으로 전환되어 3만행 이상에서 수 분씩 걸릴 수 있다.
    df['dt'] = pd.to_datetime(df['수집일자'], format='%Y-%m-%d %H:%M', errors='coerce')
    df['date_str'] = df['dt'].dt.strftime('%Y/%m/%d')
    if '중요도' not in df.columns:
        df['중요도'] = 5
    df['중요도'] = pd.to_numeric(df['중요도'], errors='coerce').fillna(5).astype(int)
    df['pub_dt'] = pd.to_datetime(df['발행일시'], format='%Y-%m-%d %H:%M', errors='coerce') if '발행일시' in df.columns else pd.NaT
    df = df[~df['분야'].isin(HIDDEN_CATEGORIES)]
    # "미분석"은 skip_ai 개발/테스트 실행이 남긴 더미 데이터라 대시보드에 절대 노출하지 않는다
    # (건수 집계에도 안 잡히게, 애초에 여기서 걸러낸다)
    df = df[df['논조'] != '미분석']

    cutoff = datetime.now(KST).replace(tzinfo=None) - timedelta(days=RECENT_DAYS_WINDOW)
    df = df[df['dt'] >= cutoff]

    return df


def load_personnel():
    path = "config/personnel.csv"
    if not os.path.exists(path):
        return []
    try:
        pdf = pd.read_csv(path, comment="#")
    except Exception:
        return []
    records = []
    for _, row in pdf.iterrows():
        records.append({
            "dept": nz(row.get("부서"), ""),
            "role": nz(row.get("직책"), ""),
            "name": nz(row.get("담당자"), ""),
            "start": None if pd.isna(row.get("시작일")) else str(row.get("시작일")),
            "end": None if pd.isna(row.get("종료일")) else str(row.get("종료일")),
            "source": None if pd.isna(row.get("출처링크")) else str(row.get("출처링크")),
            "verified": pd.isna(row.get("확인상태")) or str(row.get("확인상태")) != "자동감지",
        })
    return records


def load_cluster_warnings():
    """main.py가 남긴 cluster_warnings.csv를 읽는다 - 클러스터링이 비정상으로 커져서
    그 이슈의 기사를 저장하지 않고 건너뛴 날짜/카테고리 기록. 화면은 이걸로 카테고리
    칩 옆에 "!" 표시를 남긴다. news.json과 같은 최근 창(RECENT_DAYS_WINDOW)만 남긴다."""
    path = "cluster_warnings.csv"
    if not os.path.exists(path):
        return []
    try:
        wdf = pd.read_csv(path)
    except Exception:
        return []
    cutoff = (datetime.now(KST) - timedelta(days=RECENT_DAYS_WINDOW)).strftime("%Y-%m-%d")
    wdf = wdf[wdf["날짜"].astype(str) >= cutoff]
    return [
        {
            "date": nz(row.get("날짜"), ""),
            "category": nz(row.get("카테고리"), ""),
            "issue": nz(row.get("이슈명"), ""),
            "count": int(row["건수"]) if pd.notna(row.get("건수")) else 0,
        }
        for _, row in wdf.iterrows()
    ]


def load_bills():
    path = "bill_list.csv"
    if not os.path.exists(path):
        return []
    try:
        bdf = pd.read_csv(path)
    except Exception:
        return []
    records = []
    for _, row in bdf.iterrows():
        records.append({
            "id": nz(row.get("의안ID"), ""),
            "no": nz(row.get("의안번호"), ""),
            "name": nz(row.get("법안명"), ""),
            "category": nz(row.get("카테고리"), ""),
            "proposer": nz(row.get("제안자"), ""),
            # 위원장 발의처럼 대표발의자가 없는 건이 있다(현재 274건)
            "repProposer": nz(row.get("대표발의자"), ""),
            "proposeDate": None if pd.isna(row.get("제안일")) else str(row.get("제안일")),
            "committee": None if pd.isna(row.get("소관위원회")) else str(row.get("소관위원회")),
            "status": nz(row.get("처리상태"), ""),
            "stage": nz(row.get("처리단계"), ""),
            "result": nz(row.get("처리결과"), ""),
            "committeeDt": None if pd.isna(row.get("상임위회부일")) or not str(row.get("상임위회부일")).strip() else str(row.get("상임위회부일")),
            "committeeDoneDt": None if pd.isna(row.get("상임위처리일")) or not str(row.get("상임위처리일")).strip() else str(row.get("상임위처리일")),
            "committeeResult": None if pd.isna(row.get("상임위결과")) or not str(row.get("상임위결과")).strip() else str(row.get("상임위결과")),
            "lawDt": None if pd.isna(row.get("법사위회부일")) or not str(row.get("법사위회부일")).strip() else str(row.get("법사위회부일")),
            "lawDoneDt": None if pd.isna(row.get("법사위처리일")) or not str(row.get("법사위처리일")).strip() else str(row.get("법사위처리일")),
            "lawResult": None if pd.isna(row.get("법사위결과")) or not str(row.get("법사위결과")).strip() else str(row.get("법사위결과")),
            "changed": None if pd.isna(row.get("상태변경")) or not str(row.get("상태변경")).strip() else str(row.get("상태변경")),
            "link": nz(row.get("상세링크"), ""),
            "summary": None if pd.isna(row.get("AI요약")) else str(row.get("AI요약")),
        })
    return records


def row_to_dict(row):
    return {
        "date": nz(row["date_str"]),
        "ts": row["dt"].strftime("%Y-%m-%dT%H:%M:%S") if pd.notna(row["dt"]) else None,
        "pub_ts": row["pub_dt"].strftime("%Y-%m-%dT%H:%M:%S") if pd.notna(row["pub_dt"]) else None,
        "category": nz(row["분야"], ""),
        "issue": nz(row["대표이슈"]),
        "title": nz(row["제목"], ""),
        "press": nz(row["언론사"], ""),
        "summary": nz(row["AI요약"], ""),
        "sentiment": nz(row["논조"], ""),
        "importance": int(row["중요도"]) if pd.notna(row["중요도"]) else 5,
        "link": nz(row["기사링크"], ""),
    }


def bill_outcome_bucket(result):
    """대표발의 성과 집계용으로 처리결과를 4갈래로 묶는다.

    실제로 확인해보니(2026-08-28 기준 14,440건 중 결과가 난 216건) 그 중
    205건(95%)이 "대안반영폐기"였다 - 원안 그대로 가결되는 게 아니라, 내용이
    위원회 대안에 흡수되고 원래 법안은 형식상 폐기 처리되는 경우가 압도적으로
    많다. 이를 그냥 "폐기"(부결과 같은 취급)로 뭉치면 실질적으로는 정책이
    반영된 성과를 실패로 보이게 만든다 - 그래서 "대안반영"을 별도 갈래로 뗀다.
    "대안가결"(위원회 대안 자체가 가결된 기록)은 원안이 아니라 그 대안 쪽
    이야기라 일반 "가결"에 놔둔다."""
    if not result or result == "심사중":
        return None
    if "부결" in result:
        return "부결"
    if "대안반영" in result:
        return "대안반영"
    if "가결" in result:
        return "가결"
    if "철회" in result or "폐기" in result:
        return "폐기"
    return None


def load_member_news():
    """collect_member_news.py가 쌓은 member_news.csv를 의원 이름별로 묶는다.
    (최신순 정렬은 여기서 한 번만 해두면 화면은 그대로 앞에서부터 자르기만 하면 된다.)"""
    path = "member_news.csv"
    if not os.path.exists(path):
        return {}
    try:
        ndf = pd.read_csv(path)
    except Exception:
        return {}
    news_by_member = {}
    for _, row in ndf.iterrows():
        name = nz(row.get("의원명"), "")
        if not name:
            continue
        news_by_member.setdefault(name, []).append({
            "title": nz(row.get("제목"), ""),
            "press": nz(row.get("언론사"), ""),
            "link": nz(row.get("링크"), ""),
            "date": nz(row.get("발행일"), ""),
            "summary": nz(row.get("요약"), ""),
            "relatedCount": int(row["관련보도수"]) if pd.notna(row.get("관련보도수")) else 1,
        })
    for name in news_by_member:
        news_by_member[name].sort(key=lambda n: n["date"] or "", reverse=True)
    return news_by_member


def load_members(bills):
    """member_list.csv(22대 현역)를 읽어서 의원 검색 화면용 목록을 만든다.

    대표발의 건수와 처리결과 요약은 여기서 미리 세어 붙인다. 화면에서 세려면
    법안 목록(bills.json)이 있어야 하는데 그건 입법 탭을 볼 때만 받으므로,
    의원 탭만 열어도 보이게 한다. 세는 기준은 카드에 적히는 이름과 같은
    '대표발의자'다."""
    path = "member_list.csv"
    if not os.path.exists(path):
        return []
    try:
        mdf = pd.read_csv(path)
    except Exception:
        return []

    bill_counts = {}
    pass_results = {}
    for bill in bills:
        rep = (bill.get("repProposer") or "").strip()
        if not rep:
            continue
        bill_counts[rep] = bill_counts.get(rep, 0) + 1
        bucket = bill_outcome_bucket(bill.get("result"))
        if bucket:
            pass_results.setdefault(rep, {})
            pass_results[rep][bucket] = pass_results[rep].get(bucket, 0) + 1

    news_by_member = load_member_news()

    records = []
    for _, row in mdf.iterrows():
        name = nz(row.get("이름"), "")
        en_name = nz(row.get("영문명"), "")
        # 의원 홈페이지 프로필 주소. 영문명이 없으면 링크를 못 만드므로 빈 채로 둔다.
        profile_url = f"https://www.assembly.go.kr/members/22nd/{en_name}" if en_name else ""
        records.append({
            "name": name,
            "hanja": nz(row.get("한자명"), ""),
            "party": nz(row.get("정당"), ""),
            "prevParty": nz(row.get("이전정당"), ""),
            "term": nz(row.get("선수"), ""),
            "district": nz(row.get("지역구"), ""),
            "districtType": nz(row.get("선거구구분"), ""),
            "committees": nz(row.get("소속위원회"), ""),
            "gender": nz(row.get("성별"), ""),
            "birth": nz(row.get("생년월일"), ""),
            "tel": nz(row.get("전화"), ""),
            "email": nz(row.get("이메일"), ""),
            "homepage": nz(row.get("홈페이지"), ""),
            "office": nz(row.get("사무실"), ""),
            "aide": nz(row.get("보좌관"), ""),
            "chiefSecretary": nz(row.get("비서관"), ""),
            "secretary": nz(row.get("비서"), ""),
            "history": nz(row.get("약력"), ""),
            "photo": nz(row.get("사진"), ""),
            "billCount": bill_counts.get(name, 0),
            "passResults": pass_results.get(name) or None,
            "news": news_by_member.get(name) or None,
            "profileUrl": profile_url,
            # 지자체장·대통령 등으로 옮겨서 사실상 의정활동을 안 하는 사람의 지금 직함
            # (예: "경기도지사"). collect_bills.py의 MEMBER_ROLE_CHANGES 참고.
            "roleChange": nz(row.get("현직변경"), ""),
            # 확정판결로 의원직을 잃은 경우 - 자리를 "옮긴" 것과는 성격이
            # 달라서 roleChange와 구분해 별도로 둔다.
            "seatLost": nz(row.get("의원직상실"), ""),
        })
    # 값이 빈 칸은 키까지 빼서 가볍게 만든다(화면은 전부 truthy 검사로 쓴다)
    return [{k: v for k, v in r.items() if v not in (None, "")} for r in records]


def slim_bills(bills):
    """법안은 14,000건이 넘어 전체 용량의 대부분을 차지하므로 담을 것만 담는다.

    - 상세링크(link)는 화면에서 한 번도 쓰지 않는 데다(카드는 의안ID로 요약 팝업
      주소를 따로 만든다) 의안ID로 100% 복원되는 값이라 뺀다. 혹시 형태가 다른
      주소가 섞여 있으면 그것만 남긴다.
    - 값이 빈 칸은 키까지 통째로 뺀다. 법사위 관련 칸은 사실상 전부 비어 있고
      (회부일/처리일/결과 각 100%), 상임위처리일·결과도 98%가 비어 있다.

    이 둘로 9.8MB -> 6.2MB.
    """
    out = []
    for bill in bills:
        row = {}
        for key, value in bill.items():
            if key == "link" and value == BILL_LINK_PREFIX + str(bill.get("id", "")):
                continue
            if value is None or value == "":
                continue
            row[key] = value
        out.append(row)
    return out


def bill_stats(bills):
    """상단 '입법 현황' 타일은 법안 목록을 받기 전에도 숫자를 보여줘야 하므로,
    단계별 건수만 미리 세어 index.html 안에 같이 넣는다(수백 바이트)."""
    counts = {}
    for bill in bills:
        counts[bill.get("stage", "")] = counts.get(bill.get("stage", ""), 0) + 1
    return {
        "total": len(bills),
        "draft": counts.get("입안 및 발의", 0),
        "committee": counts.get("상임위 심사", 0),
        "law": counts.get("법사위 심사", 0),
        "plenary": counts.get("본회의 의결", 0),
    }


def sanitize(rows):
    """혹시 위에서 놓친 결측이 남아 있으면 여기서 마지막으로 걷어낸다.

    데이터는 이제 브라우저가 fetch().json() 으로 읽는데, JSON에는 `NaN`이 없어서
    딱 하나만 새어 나와도 파싱이 통째로 실패해 화면이 백지가 된다. 예전처럼
    HTML 안에 박아 넣던 시절에는 자바스크립트 리터럴로 읽혀서 그냥 통과했다.
    조용히 고치기만 하면 원인을 못 찾으니, 어느 칸이었는지 로그로 남긴다."""
    hits = {}
    for row in rows:
        for key, value in list(row.items()):
            if isinstance(value, float) and value != value:  # NaN
                row[key] = None
                hits[key] = hits.get(key, 0) + 1
    if hits:
        print(f"  ::warning:: 결측이 남아 있어 null 로 정리함: {hits}")
    return rows


def dump_json(path, rows):
    # allow_nan=False 는 위 sanitize 가 제대로 걷어냈는지 확인하는 안전장치다.
    text = json.dumps(sanitize(rows), ensure_ascii=False, allow_nan=False)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return len(text.encode("utf-8"))


def build():
    df = load_data()
    news_rows = [row_to_dict(r) for _, r in df.iterrows()] if not df.empty else []
    personnel = load_personnel()
    bills = slim_bills(load_bills())
    members = load_members(bills)
    cluster_warnings = load_cluster_warnings()

    now_kst = datetime.now(KST).strftime("%Y-%m-%d %H:%M")

    personnel_json = json.dumps(sanitize(personnel), ensure_ascii=False, allow_nan=False)
    stats_json = json.dumps(bill_stats(bills), ensure_ascii=False, allow_nan=False)

    with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
        template = f.read()

    html = template.replace("__PERSONNEL_DATA_JSON__", personnel_json.replace("</", "<\\/"))
    html = html.replace("__BILL_STATS_JSON__", stats_json.replace("</", "<\\/"))
    html = html.replace("__GENERATED_AT__", now_kst)

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    news_bytes = dump_json(NEWS_JSON_PATH, news_rows)
    bills_bytes = dump_json(BILLS_JSON_PATH, bills)
    members_bytes = dump_json(MEMBERS_JSON_PATH, members)
    warnings_bytes = dump_json(WARNINGS_JSON_PATH, cluster_warnings)
    html_kb = os.path.getsize(OUT_PATH) / 1024
    print(
        f"[정적 대시보드 생성 완료]\n"
        f"  {OUT_PATH:<20} {html_kb:8.0f}KB (인사 {len(personnel)}명)\n"
        f"  {NEWS_JSON_PATH:<20} {news_bytes/1024:8.0f}KB (최근 {RECENT_DAYS_WINDOW}일 {len(news_rows)}건)\n"
        f"  {BILLS_JSON_PATH:<20} {bills_bytes/1024:8.0f}KB (법안 {len(bills)}건)\n"
        f"  {MEMBERS_JSON_PATH:<20} {members_bytes/1024:8.0f}KB (의원 {len(members)}명)\n"
        f"  {WARNINGS_JSON_PATH:<20} {warnings_bytes/1024:8.0f}KB (클러스터링 경고 {len(cluster_warnings)}건)"
    )


if __name__ == "__main__":
    build()
