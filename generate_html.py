"""
news_list.csv + config/personnel.csv 를 읽어서 app.py(Streamlit)와 동일한 화면 구성을 갖는
정적 HTML 한 장(docs/index.html)을 생성한다.

Streamlit이 필요 없는 이유는 딱 하나 - 이 파일은 GitHub Actions(서버) 안에서만 돌고,
브라우저에서는 순수 HTML/CSS/JS로만 동작한다. 그래서 이 PC의 업로드 차단과 전혀 무관하다.
"""
import json
import os
import re
from datetime import datetime, timedelta, timezone

import pandas as pd

KST = timezone(timedelta(hours=9))

CRITICAL_KEYWORDS = ["과징금", "시정명령", "고발", "동의의결", "담합", "사익편취", "일감몰아주기", "기술탈취"]
FINE_AMOUNT_PATTERN = re.compile(r"과징금\s*([0-9][0-9,\.]*)\s*(억|만)\s*원?")
HIDDEN_CATEGORIES = ["삼성그룹", "삼성물산"]

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
    df['dt'] = pd.to_datetime(df['수집일자'], errors='coerce')
    df['date_str'] = df['dt'].dt.strftime('%Y/%m/%d')
    if '중요도' not in df.columns:
        df['중요도'] = 5
    df['중요도'] = pd.to_numeric(df['중요도'], errors='coerce').fillna(5).astype(int)
    df['pub_dt'] = pd.to_datetime(df['발행일시'], errors='coerce') if '발행일시' in df.columns else pd.NaT
    df = df[~df['분야'].isin(HIDDEN_CATEGORIES)]

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
        })
    return records


def keyword_repeat_info(titles):
    best_kw, best_count = None, 0
    for kw in CRITICAL_KEYWORDS:
        count = sum(1 for t in titles if kw in t)
        if count > best_count:
            best_kw, best_count = kw, count
    return best_kw, best_count


def extract_fine_amount(text):
    m = FINE_AMOUNT_PATTERN.search(text or "")
    return f"과징금 {m.group(1)}{m.group(2)}원" if m else None


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

    now_kst = datetime.now(KST).strftime("%Y-%m-%d %H:%M")

    data_json = json.dumps(news_rows, ensure_ascii=False)
    personnel_json = json.dumps(personnel, ensure_ascii=False)

    with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
        template = f.read()

    html = template.replace("__NEWS_DATA_JSON__", data_json.replace("</", "<\\/"))
    html = html.replace("__PERSONNEL_DATA_JSON__", personnel_json.replace("</", "<\\/"))
    html = html.replace("__GENERATED_AT__", now_kst)

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    size_kb = os.path.getsize(OUT_PATH) / 1024
    print(f"[정적 HTML 생성 완료] {OUT_PATH} (최근 {RECENT_DAYS_WINDOW}일 {len(news_rows)}건, 인사 {len(personnel)}명, 파일 크기 {size_kb:.1f}KB)")


if __name__ == "__main__":
    build()
