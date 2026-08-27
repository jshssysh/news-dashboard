"""
news_list.csv + config/personnel.csv 를 읽어서 app.py(Streamlit)와 동일한 화면 구성을 갖는
정적 HTML 한 장(docs/index.html)을 생성한다.

Streamlit이 필요 없는 이유는 딱 하나 - 이 파일은 GitHub Actions(서버) 안에서만 돌고,
브라우저에서는 순수 HTML/CSS/JS로만 동작한다. 그래서 이 PC의 업로드 차단과 전혀 무관하다.
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
TEMPLATE_PATH = "html_template.html"

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
            "dept": row.get("부서", ""),
            "role": row.get("직책", ""),
            "name": row.get("담당자", ""),
            "start": None if pd.isna(row.get("시작일")) else str(row.get("시작일")),
            "end": None if pd.isna(row.get("종료일")) else str(row.get("종료일")),
            "source": None if pd.isna(row.get("출처링크")) else str(row.get("출처링크")),
            "verified": pd.isna(row.get("확인상태")) or str(row.get("확인상태")) != "자동감지",
        })
    return records


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
            "id": row.get("의안ID", ""),
            "no": row.get("의안번호", ""),
            "name": row.get("법안명", ""),
            "category": row.get("카테고리", ""),
            "proposer": row.get("제안자", ""),
            "repProposer": row.get("대표발의자", ""),
            "proposeDate": None if pd.isna(row.get("제안일")) else str(row.get("제안일")),
            "committee": None if pd.isna(row.get("소관위원회")) else str(row.get("소관위원회")),
            "status": row.get("처리상태", ""),
            "stage": row.get("처리단계", ""),
            "result": row.get("처리결과", ""),
            "committeeDt": None if pd.isna(row.get("상임위회부일")) or not str(row.get("상임위회부일")).strip() else str(row.get("상임위회부일")),
            "committeeDoneDt": None if pd.isna(row.get("상임위처리일")) or not str(row.get("상임위처리일")).strip() else str(row.get("상임위처리일")),
            "committeeResult": None if pd.isna(row.get("상임위결과")) or not str(row.get("상임위결과")).strip() else str(row.get("상임위결과")),
            "lawDt": None if pd.isna(row.get("법사위회부일")) or not str(row.get("법사위회부일")).strip() else str(row.get("법사위회부일")),
            "lawDoneDt": None if pd.isna(row.get("법사위처리일")) or not str(row.get("법사위처리일")).strip() else str(row.get("법사위처리일")),
            "lawResult": None if pd.isna(row.get("법사위결과")) or not str(row.get("법사위결과")).strip() else str(row.get("법사위결과")),
            "changed": None if pd.isna(row.get("상태변경")) or not str(row.get("상태변경")).strip() else str(row.get("상태변경")),
            "link": row.get("상세링크", ""),
            "summary": None if pd.isna(row.get("AI요약")) else str(row.get("AI요약")),
        })
    return records


def row_to_dict(row):
    return {
        "date": row["date_str"] if pd.notna(row["date_str"]) else None,
        "ts": row["dt"].strftime("%Y-%m-%dT%H:%M:%S") if pd.notna(row["dt"]) else None,
        "pub_ts": row["pub_dt"].strftime("%Y-%m-%dT%H:%M:%S") if pd.notna(row["pub_dt"]) else None,
        "category": row["분야"],
        "issue": row["대표이슈"],
        "title": row["제목"],
        "press": row["언론사"],
        "summary": row["AI요약"],
        "sentiment": row["논조"],
        "importance": int(row["중요도"]) if pd.notna(row["중요도"]) else 5,
        "link": row["기사링크"],
    }


def build():
    df = load_data()
    news_rows = [row_to_dict(r) for _, r in df.iterrows()] if not df.empty else []
    personnel = load_personnel()
    bills = load_bills()

    now_kst = datetime.now(KST).strftime("%Y-%m-%d %H:%M")

    data_json = json.dumps(news_rows, ensure_ascii=False)
    personnel_json = json.dumps(personnel, ensure_ascii=False)
    bills_json = json.dumps(bills, ensure_ascii=False)

    with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
        template = f.read()

    html = template.replace("__NEWS_DATA_JSON__", data_json.replace("</", "<\\/"))
    html = html.replace("__PERSONNEL_DATA_JSON__", personnel_json.replace("</", "<\\/"))
    html = html.replace("__BILL_DATA_JSON__", bills_json.replace("</", "<\\/"))
    html = html.replace("__GENERATED_AT__", now_kst)

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    size_kb = os.path.getsize(OUT_PATH) / 1024
    print(f"[정적 HTML 생성 완료] {OUT_PATH} (최근 {RECENT_DAYS_WINDOW}일 {len(news_rows)}건, 인사 {len(personnel)}명, 법안 {len(bills)}건, 파일 크기 {size_kb:.1f}KB)")


if __name__ == "__main__":
    build()
