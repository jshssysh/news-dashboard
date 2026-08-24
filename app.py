import re
from urllib.parse import urlparse

import streamlit as st
import pandas as pd
from datetime import datetime, timedelta

# 기사 본문(제목+요약)에 등장하면 "선별 근거" 칩으로 표시할 중대 키워드
CRITICAL_KEYWORDS = ["과징금", "시정명령", "고발", "동의의결", "담합", "사익편취", "일감몰아주기", "기술탈취"]
FINE_AMOUNT_PATTERN = re.compile(r"과징금\s*([0-9][0-9,\.]*)\s*(억|만)\s*원?")

# 앱 화면에서 숨길 분야 (수집·분류는 main.py에서 그대로 하되, 화면에는 노출하지 않음)
HIDDEN_CATEGORIES = ["삼성그룹", "삼성물산"]


def extract_domain(url):
    try:
        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return ""


def extract_fine_amount(text):
    m = FINE_AMOUNT_PATTERN.search(text or "")
    return f"과징금 {m.group(1)}{m.group(2)}원" if m else None


def keyword_repeat_info(rows):
    """이슈 그룹 내 여러 기사 제목에 걸쳐 가장 많이 반복되는 중대 키워드와 그 횟수를 찾는다.
    (조작된 점수가 아니라, 실제로 여러 매체 제목에 같은 키워드가 몇 번 등장하는지를 센 값)"""
    titles = rows["제목"].tolist()
    best_kw, best_count = None, 0
    for kw in CRITICAL_KEYWORDS:
        count = sum(1 for t in titles if kw in t)
        if count > best_count:
            best_kw, best_count = kw, count
    return best_kw, best_count


def selection_reasons(g):
    reasons = []
    if g["press_count"] >= 5:
        reasons.append(f"반복 보도 {g['press_count']}건")
    kw, count = g["kw_repeat"]
    if kw and count >= 2:
        reasons.append(f"'{kw}' {count}회 반복")
    if g["category"] == "제재·심결":
        reasons.append("제재·심결 신호")
    return reasons

st.set_page_config(page_title="Daily Brief", layout="wide", initial_sidebar_state="collapsed")

# Streamlit 내부 테마 변수(--primary-color 등)가 버전에 따라 값이 비거나 이름이 달라 커스텀 요소에
# 안 먹히는 경우가 있어서, 라이트/다크는 브라우저의 prefers-color-scheme으로 직접 판단해 우리만의
# 색상 토큰(--app-*)을 정의하고 그것만 사용한다. (Streamlit 기본 배경/텍스트 색은 이 앱 CSS와 무관하게
# Streamlit이 자체적으로 처리하므로 그대로 둔다)
st.markdown("""
<style>
:root {
    --app-primary: #ff4b4b;
    --app-secondary-bg: #f0f2f6;
    --app-text: #31333f;
}
@media (prefers-color-scheme: dark) {
    :root {
        --app-primary: #ff6b6b;
        --app-secondary-bg: #262730;
        --app-text: #fafafa;
    }
}
.block-container { max-width: 1100px; padding-top: 3rem; }

.app-header { display: flex; align-items: center; gap: 9px; padding: 2px 0; }
.app-logo { background: linear-gradient(135deg, var(--app-primary), #ff8a65); color: #fff; font-weight: 800; width: 26px; height: 26px; border-radius: 7px; display: flex; align-items: center; justify-content: center; font-size: 0.85em; flex-shrink: 0; box-shadow: 0 2px 6px rgba(255,75,75,0.35); }
.app-title { font-size: 1.05em; font-weight: 800; letter-spacing: -0.2px; line-height: 1.15; color: var(--app-text); }

/* 상단 바(제목+탭+날짜) 한 줄: 좁은 화면에서도 줄바꿈 없이 유지, 버튼/텍스트는 작게
   내용 크기 기반 자동 축소가 Streamlit 내부 스타일과 충돌해 안 먹혀서, 각 칸의 폭을 직접 고정값으로 지정 */
/* data-testid 값은 Streamlit 버전마다 달라질 수 있어(column/stColumn 등) 이름에 의존하지 않고
   "가로 블록의 직계 자식 div"라는 구조만으로 컬럼을 지정한다 */

/* 상단 통계 줄(Daily Brief 타일 + 수집기사/주요뉴스/보도확산 타일): 네 칸 높이를 통일 */
.st-key-top_stat_row div[data-testid="stHorizontalBlock"] { align-items: stretch !important; }
.st-key-top_stat_row div[data-testid="stHorizontalBlock"] > div { display: flex !important; }
.st-key-top_stat_row div[data-testid="stHorizontalBlock"] > div > div { width: 100%; }

/* Daily Brief 타일: 카드 모양 통일, 왼쪽은 제목, 오른쪽 위는 탭, 오른쪽 아래는 날짜 */
.st-key-db_tile { background-color: var(--app-secondary-bg); border: 1px solid rgba(128,128,128,0.3); border-radius: 10px; padding: 12px 16px; height: 100%; box-sizing: border-box; display: flex; flex-direction: column; justify-content: center; }
.st-key-db_tile div[data-testid="stHorizontalBlock"] { align-items: center !important; }
.st-key-db_title_row div[data-testid="stHorizontalBlock"] { display: flex !important; flex-direction: row !important; flex-wrap: nowrap !important; align-items: center !important; gap: 1px !important; }
.st-key-db_title_row div[data-testid="stHorizontalBlock"] > div { min-width: 0 !important; }
.st-key-db_title_row div[data-testid="stHorizontalBlock"] > div:first-child { flex: 0 0 auto !important; width: 82px !important; }
.st-key-db_title_row div[data-testid="stHorizontalBlock"] > div:last-child { flex: 1 1 auto !important; width: auto !important; }
/* 탭 3개는 글자를 감싸는 크기로(내용에 맞춤), 사이 간격은 1px */
.st-key-db_tabs_row div[data-testid="stHorizontalBlock"] { display: flex !important; flex-wrap: nowrap !important; gap: 1px !important; margin-bottom: 8px; justify-content: flex-start; }
.st-key-db_tabs_row div[data-testid="stHorizontalBlock"] > div { flex: 0 0 auto !important; min-width: 0 !important; width: auto !important; }
.st-key-db_tabs_row button {
    padding: 4px 10px !important; font-size: 0.72em !important; white-space: nowrap; border-radius: 20px !important; width: auto !important;
}
.st-key-db_date_row div[data-testid="stHorizontalBlock"] { display: flex !important; flex-direction: row !important; flex-wrap: nowrap !important; align-items: center !important; gap: 4px !important; justify-content: flex-start; }
.st-key-db_date_row div[data-testid="stHorizontalBlock"] > div { flex: 0 0 auto !important; min-width: 0 !important; width: auto !important; }
.st-key-db_date_row button {
    padding: 4px 10px !important; font-size: 0.72em !important; white-space: nowrap; border-radius: 20px !important; width: 100% !important;
}
/* 날짜 텍스트도 화살표 버튼과 같은 타원 모양으로, 가운데 정렬 */
.date-pill { display: flex; align-items: center; justify-content: center; background-color: rgba(128,128,128,0.18); border-radius: 20px; padding: 4px 10px; font-weight: 600; font-size: 0.72em; color: var(--app-text); white-space: nowrap; }

/* 카테고리별 수집 현황 패널: 본문을 스크롤해도 화면에 붙어서 따라옴
   (Streamlit이 컬럼/블록에 자체적으로 overflow를 걸어두면 sticky가 무효화되므로 명시적으로 풀어줌) */
div[data-testid="stHorizontalBlock"]:has(.st-key-side_sticky),
div[data-testid="stHorizontalBlock"]:has(.st-key-side_sticky) > div,
div[data-testid="stHorizontalBlock"]:has(.st-key-side_sticky) div[data-testid="stVerticalBlock"],
div[data-testid="stHorizontalBlock"]:has(.st-key-side_sticky) div[data-testid="stVerticalBlockBorderWrapper"] {
    overflow: visible !important;
    height: auto !important;
}
div[data-testid="stHorizontalBlock"]:has(.st-key-side_sticky) {
    align-items: stretch !important;
}
.st-key-side_sticky {
    position: -webkit-sticky !important;
    position: sticky !important;
    top: 20px !important;
    align-self: flex-start !important;
    z-index: 1 !important;
}


/* 표시 개수/페이지 번호 버튼: 글자를 더 두껍게, 중앙 정렬, 세로 높이를 줄임 */
.st-key-side_pagination button {
    font-weight: 800 !important;
    padding: 2px 4px !important;
    display: flex !important; align-items: center !important; justify-content: center !important;
    text-align: center !important;
}
.st-key-side_pagination div[data-baseweb="select"] { font-weight: 800; }
.app-header { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

/* 카테고리별 주요뉴스 칩: 이름만 표시하고, 대표 이슈/중요도/건수는 마우스를 올리면 툴팁으로 보임.
   넘치면 가로 스크롤 대신 다음 줄로 자동 줄바꿈 */
.st-key-cat_chip_row div[data-testid="stHorizontalBlock"] { display: flex !important; flex-wrap: wrap !important; gap: 4px !important; }
.st-key-cat_chip_row div[data-testid="stHorizontalBlock"] > div { flex: 0 0 auto !important; min-width: 0 !important; width: auto !important; }
.st-key-cat_chip_row button { border-radius: 20px !important; white-space: nowrap !important; padding: 6px 16px !important; width: auto !important; }

/* 카테고리별 주요뉴스 제목 옆 필터 줄: 드롭다운 3개는 고정 폭으로 좁히고
   (내용 크기 맞춤(auto)은 Streamlit 내부 스타일에 밀려 무시됨 - 상단 바와 같은 방식으로 px 고정),
   남는 폭은 전부 검색창이 가져감 */
.st-key-news_filter_row div[data-testid="stHorizontalBlock"] { display: flex !important; flex-wrap: nowrap !important; align-items: center !important; gap: 8px !important; }
.st-key-news_filter_row div[data-testid="stHorizontalBlock"] > div { min-width: 0 !important; flex: 0 0 auto !important; }
.st-key-news_filter_row div[data-testid="stHorizontalBlock"] > div:nth-child(1) { width: 230px !important; }
.st-key-news_filter_row div[data-testid="stHorizontalBlock"] > div:nth-child(2) { width: 100px !important; }
.st-key-news_filter_row div[data-testid="stHorizontalBlock"] > div:nth-child(3) { width: 100px !important; }
.st-key-news_filter_row div[data-testid="stHorizontalBlock"] > div:nth-child(4) { width: 110px !important; }
.st-key-news_filter_row div[data-testid="stHorizontalBlock"] > div:nth-child(5) { flex: 1 1 auto !important; width: auto !important; }
.st-key-news_filter_row div[data-baseweb="select"] * { min-width: 0 !important; }

/* 배지 및 칩 (라이트/다크 공통 — 배지 자체는 항상 옅은 색 배경 + 짙은 텍스트라 어느 테마에서도 읽힘) */
.badge-positive { background-color: #e8f5e9; color: #2e7d32; padding: 2px 8px; border-radius: 4px; font-weight: bold; font-size: 0.85em; margin-right: 8px;}
.badge-neutral { background-color: #fff3cd; color: #8a6100; padding: 2px 8px; border-radius: 4px; font-weight: bold; font-size: 0.85em; margin-right: 8px;}
.badge-negative { background-color: #fdecea; color: #c62828; padding: 2px 8px; border-radius: 4px; font-weight: bold; font-size: 0.85em; margin-right: 8px;}
.badge-fail { background-color: #f3e5f5; color: #7b1fa2; padding: 2px 8px; border-radius: 4px; font-weight: bold; font-size: 0.85em; margin-right: 8px;}
.chip-category { background-color: var(--app-secondary-bg); color: var(--app-text); padding: 2px 8px; border-radius: 4px; font-weight: bold; font-size: 0.85em; margin-right: 8px; border: 1px solid rgba(128,128,128,0.3); }
.chip-tag-warn { background-color: #fdecea; color: #c62828; padding: 2px 8px; border-radius: 4px; font-weight: bold; font-size: 0.85em; margin-right: 8px; }
.chip-alert { background-color: #c62828; color: #fff; padding: 2px 8px; border-radius: 4px; font-weight: bold; font-size: 0.8em; margin-right: 8px; }
.card-meta { font-size: 0.8em; opacity: 0.6; margin: 4px 0 10px 0; }
.card-meta-inline { font-size: 0.8em; opacity: 0.6; margin-left: 8px; }
.reason-chip { display: inline-block; background-color: var(--app-secondary-bg); color: var(--app-text); opacity: 0.85; padding: 1px 7px; border-radius: 4px; font-size: 0.85em; font-weight: 700; margin-right: 4px; border: 1px solid rgba(128,128,128,0.25); }
.reason-box { border: 1px solid rgba(128,128,128,0.3); border-radius: 8px; padding: 4px 10px; display: flex; align-items: center; flex-wrap: wrap; gap: 4px; height: 100%; box-sizing: border-box; line-height: 1.3; }
.stButton button { white-space: nowrap; }
/* 유사 기사 토글 버튼: 선별 근거 박스와 정확히 같은 크기·굵기로 맞춤 */
div[class*="st-key-toggle_row_"] button {
    font-size: 0.85em !important; font-weight: 700 !important;
    padding: 4px 10px !important; min-height: 0 !important; line-height: 1.3 !important;
}
/* 기사 카드 내부 요소 사이 여백을 줄여 카드 전체 높이를 줄임 */
div[class*="st-key-article_card_"] [data-testid="stVerticalBlock"] > div { margin-bottom: 2px !important; }

/* AI 요약 / 오늘 주요 내용은 항상 짙은 네이비 강조 카드로 고정 (테마와 무관한 브랜드 악센트) */
.summary-box-blue { background-color: #0d1e36; padding: 12px; border-radius: 8px; margin-bottom: 4px; font-size: 0.95em; color: #8ab4f8; }
.signal-box { background-color: #12203a; border: 1px solid #12203a; border-radius: 10px; padding: 16px; margin-bottom: 15px; }
.signal-tag { display: inline-block; background-color: #24406e; color: #8ab4f8; font-size: 0.75em; padding: 2px 8px; border-radius: 4px; }
.signal-item { margin-top: 12px; padding-top: 12px; border-top: 1px solid rgba(255,255,255,0.15); }
.signal-item:first-of-type { margin-top: 10px; padding-top: 0; border-top: none; }
.signal-num { display: inline-block; background-color: #8ab4f8; color: #0d1e36; font-weight: bold; width: 18px; height: 18px; border-radius: 50%; text-align: center; font-size: 0.75em; line-height: 18px; margin-right: 4px; }
.signal-item-head { color: #fff; font-size: 0.95em; display: flex; align-items: center; flex-wrap: wrap; gap: 4px; }
.signal-item-body { color: #cbd5e1; font-size: 0.85em; margin-top: 4px; line-height: 1.4; }
.chip-category-dark { background-color: #24406e; color: #8ab4f8; padding: 1px 6px; border-radius: 4px; font-size: 0.8em; margin: 0 4px; }
.reason-chip-dark { display: inline-block; background-color: #24406e; color: #8ab4f8; opacity: 0.9; padding: 1px 7px; border-radius: 4px; font-size: 0.75em; margin-right: 4px; }

/* 통계 타일 */
.stat-card { background-color: var(--app-secondary-bg); border: 1px solid rgba(128,128,128,0.3); border-radius: 10px; padding: 12px 16px; height: 100%; box-sizing: border-box; }
.stat-card .label { font-size: 0.8em; color: var(--app-text); opacity: 0.65; margin-bottom: 4px; }
.stat-card .value-line { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
.stat-card .value { font-size: 1.75em; font-weight: 800; color: var(--app-text); letter-spacing: -0.3px; }
.stat-card .sub-inline { font-size: 0.72em; color: var(--app-text); opacity: 0.6; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.stat-card .sentiment-mini { font-size: 0.68em; color: var(--app-text); opacity: 0.75; margin-top: 4px; }
.stat-card .sentiment-mini .sm-pos { color: #43a047; font-weight: 700; }
.stat-card .sentiment-mini .sm-neu { color: #d99a2b; font-weight: 700; }
.stat-card .sentiment-mini .sm-neg { color: #e05353; font-weight: 700; }
.stat-card .sentiment-mini .sm-fail { color: #7b1fa2; font-weight: 700; }

/* 사이드바 공통 패널 */
.sidebar-panel { background-color: var(--app-secondary-bg); border: 1px solid rgba(128,128,128,0.3); border-radius: 10px; padding: 14px 10px; margin-bottom: 14px; }
.sidebar-panel .panel-title { font-weight: bold; color: var(--app-text); margin-bottom: 8px; }

.kw-row { margin-bottom: 20px; }
.kw-row .kw-label { font-size: 0.85em; color: var(--app-text); font-weight: bold; margin-bottom: 6px; }
.kw-total { opacity: 0.65; font-weight: normal; }
.cnt-neg { color: #e05353; font-weight: 700; }
.cnt-neu { color: #d99a2b; font-weight: 700; }
.cnt-pos { color: #43a047; font-weight: 700; }
/* 막대는 얇게 유지하고, 글자는 막대 위아래로 튀어나오는 걸 허용해 크게 보이도록 함.
   글자색과 막대색이 같아 안 보이는 걸 막기 위해 흰 테두리(halo)를 둘러 대비를 준다 */
.sentiment-track { display: flex; width: 100%; height: 6px; border-radius: 6px; overflow: visible; background-color: rgba(128,128,128,0.2); }
.sentiment-seg {
    display: flex; align-items: center; justify-content: center; font-size: 0.8em; font-weight: 700; white-space: nowrap;
    -webkit-text-stroke: 3px #fff;
    paint-order: stroke fill;
    text-shadow: -1px -1px 0 #fff, 1px -1px 0 #fff, -1px 1px 0 #fff, 1px 1px 0 #fff, 0 0 4px #fff;
}
.sentiment-neg { background-color: #e05353; color: #e05353; }
.sentiment-neu { background-color: #e0a940; color: #d99a2b; }
.sentiment-pos { background-color: #4caf82; color: #43a047; }
</style>
""", unsafe_allow_html=True)


@st.cache_data(ttl=600)
def load_data():
    try:
        df = pd.read_csv("news_list.csv")
        df['dt'] = pd.to_datetime(df['수집일자'], errors='coerce')
        df['date_str'] = df['dt'].dt.strftime('%Y/%m/%d')
        if '중요도' not in df.columns:
            df['중요도'] = 5  # 구버전 데이터(중요도 컬럼 없음) 호환
        df['중요도'] = pd.to_numeric(df['중요도'], errors='coerce').fillna(5)
        # 실제 기사 발행 시각 (구버전 데이터엔 없음 → NaT, 카드에서 수집 시각으로 대체 표시)
        df['pub_dt'] = pd.to_datetime(df['발행일시'], errors='coerce') if '발행일시' in df.columns else pd.NaT
        # 앱 화면에서는 삼성그룹·삼성물산 분야를 숨김 (수집·분류 자체는 main.py에서 그대로 유지)
        df = df[~df['분야'].isin(HIDDEN_CATEGORIES)]
        return df
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=600)
def load_personnel():
    try:
        df = pd.read_csv("config/personnel.csv", comment="#")
        df["시작일"] = pd.to_datetime(df["시작일"], errors="coerce")
        df["종료일"] = pd.to_datetime(df["종료일"], errors="coerce")
        return df
    except Exception:
        return pd.DataFrame()


def build_issue_groups(source_df):
    groups = []
    for title, gdf in source_df.groupby('대표이슈'):
        gdf_sorted = gdf.sort_values('중요도', ascending=False)
        rep = gdf_sorted.iloc[0]
        groups.append({
            'title': title,
            'importance': int(rep['중요도']),
            'sentiment': rep['논조'],
            'category': rep['분야'],
            'summary': rep['AI요약'],
            'main_press': rep['언론사'],
            'press_count': len(gdf_sorted),
            'rep_dt': rep['dt'],
            'rep_pub_dt': rep['pub_dt'],
            'rep_link': rep['기사링크'],
            'kw_repeat': keyword_repeat_info(gdf_sorted),
            'rows': gdf_sorted,
        })
    # AI 중요도가 같으면, 여러 매체 제목에 걸쳐 같은 키워드가 더 많이 반복되는(=더 확실한 신호) 쪽을 우선한다
    groups.sort(key=lambda g: (g['importance'], g['kw_repeat'][1]), reverse=True)
    return groups


def badge_class(sentiment):
    return {'긍정': 'badge-positive', '부정': 'badge-negative', '판단 실패': 'badge-fail'}.get(sentiment, 'badge-neutral')


df = load_data()

if 'current_date' not in st.session_state:
    st.session_state.current_date = df['dt'].max().date() if not df.empty else datetime.now().date()
if 'active_tab' not in st.session_state:
    st.session_state.active_tab = '뉴스'

daily_df = df[df['date_str'] == st.session_state.current_date.strftime('%Y/%m/%d')] if not df.empty else pd.DataFrame()

if not daily_df.empty:
    all_issue_groups = build_issue_groups(daily_df)
    major_count = int((daily_df['중요도'] >= 6).sum())
    spread_groups = [g for g in all_issue_groups if g['press_count'] >= 5]
    spread_count = len(spread_groups)
    sentiment_all_counts = daily_df['논조'].value_counts()
    daily_category_counts = daily_df['분야'].value_counts()
    # 수집기사 = 오늘 수집된 전체 기사 수, 중복 = 같은 이슈로 묶여 반복 보도된 기사 수(전체 - 고유 이슈 수)
    dup_count = len(daily_df) - len(all_issue_groups)
    # 보도확산 타일에 표시할, 가장 많은 매체가 다룬 이슈 한 줄 요약
    most_spread = max(all_issue_groups, key=lambda g: g['press_count'])
    spread_caption = most_spread['title']
    if len(spread_caption) > 16:
        spread_caption = spread_caption[:16] + '…'
else:
    all_issue_groups = []
    major_count = spread_count = dup_count = 0
    sentiment_all_counts = pd.Series(dtype='int64')
    daily_category_counts = pd.Series(dtype='int64')
    spread_caption = ""

sentiment_mini_items = [
    f"<span class='sm-pos'>긍정 {sentiment_all_counts.get('긍정', 0)}</span>",
    f"<span class='sm-neu'>중립 {sentiment_all_counts.get('중립', 0)}</span>",
    f"<span class='sm-neg'>부정 {sentiment_all_counts.get('부정', 0)}</span>",
    f"<span class='sm-fail'>판단실패 {sentiment_all_counts.get('판단 실패', 0)}</span>",
]
if sentiment_all_counts.get('미분석', 0):
    sentiment_mini_items.append(f"<span class='sm-fail'>미분석 {sentiment_all_counts.get('미분석', 0)}</span>")
sentiment_mini_html = " · ".join(sentiment_mini_items)

# ---- 상단 한 줄: Daily Brief(탭+날짜) 타일 + 수집기사/주요뉴스/보도확산 타일 ----
with st.container(key="top_stat_row"):
    tile_cols = st.columns([1.6, 1, 1, 1], gap="small")

    with tile_cols[0]:
        with st.container(key="db_tile"):
            with st.container(key="db_title_row"):
                db_left, db_right = st.columns([1.4, 1.6])
                with db_left:
                    st.markdown("<div class='app-header'><span class='app-logo'>D</span><span class='app-title'>Daily<br>Brief</span></div>", unsafe_allow_html=True)
                with db_right:
                    with st.container(key="db_tabs_row"):
                        tab_cols = st.columns(3)
                        for label, col in zip(['뉴스', '입법', '공정위 조직'], tab_cols):
                            with col:
                                is_active = st.session_state.active_tab == label
                                if st.button(label, key=f"tab_{label}", type="primary" if is_active else "secondary"):
                                    st.session_state.active_tab = label
                    with st.container(key="db_date_row"):
                        d_prev, d_text, d_next = st.columns([1, 2, 1])
                        with d_prev:
                            if st.button("◀", key="date_prev", use_container_width=True):
                                st.session_state.current_date -= timedelta(days=1)
                                st.rerun()
                        with d_text:
                            date_display = st.session_state.current_date.strftime('%Y/%m/%d')
                            st.markdown(f"<div class='date-pill'>{date_display}</div>", unsafe_allow_html=True)
                        with d_next:
                            if st.button("▶", key="date_next", use_container_width=True):
                                st.session_state.current_date += timedelta(days=1)
                                st.rerun()

    with tile_cols[1]:
        st.markdown(f"""
<div class="stat-card"><div class="label">수집 기사</div><div class="value-line"><span class="value">{len(daily_df)}건</span><span class="sub-inline">중복 {dup_count}건</span></div><div class="sentiment-mini">{sentiment_mini_html}</div></div>
""", unsafe_allow_html=True)
    with tile_cols[2]:
        st.markdown(f"""
<div class="stat-card"><div class="label">주요 뉴스</div><div class="value-line"><span class="value">{major_count}건</span><span class="sub-inline">AI 판단 기준</span></div></div>
""", unsafe_allow_html=True)
    with tile_cols[3]:
        st.markdown(f"""
<div class="stat-card"><div class="label">보도 확산</div><div class="value-line"><span class="value">{spread_count}건</span><span class="sub-inline">{spread_caption} 등</span></div></div>
""", unsafe_allow_html=True)

if st.session_state.active_tab == '공정위 조직':
    st.caption("부서·직책별 담당자 재임 이력 (수동 기록 · config/personnel.csv에서 직접 추가/수정 가능)")
    personnel_df = load_personnel()
    if personnel_df.empty:
        st.info("아직 등록된 인사 이력이 없습니다. config/personnel.csv에 추가해주세요.")
    else:
        for (dept, role), grp in personnel_df.groupby(['부서', '직책']):
            grp = grp.sort_values('시작일', ascending=False)
            st.markdown(f"##### {dept} · {role}")
            for _, row in grp.iterrows():
                is_current = pd.isna(row['종료일'])
                start = row['시작일'].strftime('%Y.%m.%d') if pd.notna(row['시작일']) else '?'
                end_display = '현재' if is_current else row['종료일'].strftime('%Y.%m.%d')
                current_badge = "<span class='badge-positive'>현재</span> " if is_current else ""
                st.markdown(f"<div style='margin-bottom:6px;'>{current_badge}<b>{row['담당자']}</b> · {start} ~ {end_display}</div>", unsafe_allow_html=True)
            st.write("")
    st.stop()
elif st.session_state.active_tab != '뉴스':
    st.info(f"'{st.session_state.active_tab}' 섹션은 준비 중입니다. 곧 추가될 예정입니다.")
    st.stop()

if daily_df.empty:
    st.info("해당 날짜에 수집된 기사 데이터가 없습니다.")
    st.stop()

st.markdown("<hr style='margin: 4px 0; border-color: rgba(128,128,128,0.3);'>", unsafe_allow_html=True)

# 오늘 주요 내용 (규칙 기반 — 중요도 상위 3개 이슈. 별도 AI 호출 없이 수집된 데이터로 자동 생성)
top3 = all_issue_groups[:3]
signal_items = ""
for i, g in enumerate(top3, start=1):
    top3_reasons = selection_reasons(g)
    top3_reason_chips = " ".join(f"<span class='reason-chip-dark'>{r}</span>" for r in top3_reasons)
    signal_items += f"""
<div class="signal-item">
    <div class="signal-item-head"><span class="signal-num">{i}</span><span class="chip-category-dark">{g['category']}</span><a href="{g['rep_link']}" target="_blank" style="color:inherit; text-decoration:none;"><b>{g['title']}</b></a> {top3_reason_chips}</div>
    <a href="{g['rep_link']}" target="_blank" style="text-decoration:none; color:inherit; display:block;"><div class="signal-item-body">{g['summary']} (관련 보도 {g['press_count']}건)</div></a>
</div>
"""

st.markdown(f"""
<div class="signal-box">
    <span class="signal-tag">오늘 주요 내용</span>
    {signal_items}
</div>
""", unsafe_allow_html=True)

# 카테고리별 주요뉴스 + 카테고리 필터 칩을 하나로 병합 (클릭하면 아래 목록이 그 카테고리로 필터링됨)
# 검색/논조/정렬/기간 필터도 이 제목 줄 우측에 모아 배치
with st.container(key="news_filter_row"):
    head_col1, head_col2, head_col3, head_col4, head_col5 = st.columns([2, 1, 1, 1, 2])
    with head_col1:
        st.markdown("##### 카테고리별 주요뉴스")
    with head_col2:
        period_choice = st.selectbox("기간", options=['오늘만', '최근 7일', '최근 30일'], label_visibility="collapsed")
    with head_col3:
        sort_option = st.selectbox("정렬", options=['중요도순', '보도량순', '최신순'], label_visibility="collapsed")
    with head_col4:
        sentiment_filter = st.selectbox("논조", options=['논조 전체', '긍정', '중립', '부정', '판단 실패'], label_visibility="collapsed")
    with head_col5:
        search_text = st.text_input("검색", placeholder="제목, 요약에서 검색", label_visibility="collapsed")

# 기간 선택에 따라 검색 대상 범위를 넓힘 (상단 통계·오늘의 신호는 항상 선택된 날짜 하루 기준 유지)
period_days = {'오늘만': 0, '최근 7일': 6, '최근 30일': 29}[period_choice]
if period_days == 0:
    scoped_df = daily_df
else:
    range_start = st.session_state.current_date - timedelta(days=period_days)
    scoped_df = df[(df['dt'].dt.date >= range_start) & (df['dt'].dt.date <= st.session_state.current_date)]

category_counts = scoped_df['분야'].value_counts()
categories = ['전체'] + list(category_counts.index)
chip_counts = {'전체': len(scoped_df), **{c: category_counts[c] for c in category_counts.index}}

# 카테고리별 오늘의 대표 이슈(중요도 1위) - 카드에는 이름만, 나머지는 마우스를 올리면 툴팁으로 표시
top_issue_by_cat = {}
for g in all_issue_groups:
    if g['category'] not in top_issue_by_cat:
        top_issue_by_cat[g['category']] = g

if 'selected_category' not in st.session_state:
    st.session_state.selected_category = '전체'
if st.session_state.selected_category not in categories:
    st.session_state.selected_category = '전체'

with st.container(key="cat_chip_row"):
    chip_cols = st.columns(len(categories))
    for i, cat in enumerate(categories):
        with chip_cols[i]:
            if cat in top_issue_by_cat:
                tg = top_issue_by_cat[cat]
                tip = f"**{tg['title']}** · 중요도 {tg['importance']}/10 · {chip_counts[cat]}건"
            else:
                tip = f"{chip_counts[cat]}건"
            is_selected = st.session_state.selected_category == cat
            if st.button(cat, key=f"catchip_{cat}", type=("primary" if is_selected else "secondary"), help=tip):
                st.session_state.selected_category = cat
                st.rerun()
selected_category = st.session_state.selected_category

# 기본 st.divider()는 위아래 여백이 너무 커서, 기사 카드 사이 간격 정도로 줄인 얇은 선으로 대체
st.markdown("<hr style='margin: 4px 0; border-color: rgba(128,128,128,0.3);'>", unsafe_allow_html=True)

# 데스크탑: 본문(3) + 사이드바(1) 2단 구성. 화면이 좁아지면 Streamlit이 자동으로 세로 1단으로 쌓는다.
main_col, side_col = st.columns([2.85, 1.0], gap="medium")

with main_col:
    # 필터 적용 (검색/논조/정렬/기간은 위 "카테고리별 주요뉴스" 줄로 이동)
    filtered_df = scoped_df if selected_category == '전체' else scoped_df[scoped_df['분야'] == selected_category]
    if sentiment_filter != '논조 전체':
        filtered_df = filtered_df[filtered_df['논조'] == sentiment_filter]
    if search_text:
        mask = (
            filtered_df['제목'].str.contains(search_text, case=False, na=False)
            | filtered_df['AI요약'].str.contains(search_text, case=False, na=False)
            | filtered_df['대표이슈'].str.contains(search_text, case=False, na=False)
        )
        filtered_df = filtered_df[mask]

    issue_groups = build_issue_groups(filtered_df) if not filtered_df.empty else []
    issue_groups = [g for g in issue_groups if g['main_press'] != '미상']  # 대표 언론사를 못 찾은 이슈는 제외

    if sort_option == '최신순':
        issue_groups.sort(key=lambda g: g['rows']['dt'].max(), reverse=True)
    elif sort_option == '보도량순':
        issue_groups.sort(key=lambda g: g['press_count'], reverse=True)
    # '중요도순'은 build_issue_groups가 이미 그 순서로 정렬해서 반환함

total_count = len(issue_groups)
page_size_options = [10, 20, 50]
if "list_page" not in st.session_state:
    st.session_state.list_page = 1

# 표시 개수 선택(위젯)은 아래 side_pagination에서 렌더링되지만, 그 값을 카테고리별 수집 현황
# 위쪽 캡션에서 먼저 써야 해서 session_state에 남아있는 이전 값을 미리 읽어온다
_page_size_for_caption = st.session_state.get("pg_size_select", page_size_options[0])
_page_for_caption = st.session_state.list_page


def sentiment_seg_html(pct, css_class):
    if pct <= 0:
        return ""
    return f"<div class='sentiment-seg {css_class}' style='width:{pct}%;'>{pct}%</div>"


with side_col:
    if issue_groups:
        _start_for_caption = (_page_for_caption - 1) * _page_size_for_caption
        _total_pages_for_caption = max(1, -(-total_count // _page_size_for_caption))
        st.caption(f"전체 {total_count}건 중 {_start_for_caption + 1}~{min(_start_for_caption + _page_size_for_caption, total_count)}건 표시 (총 {_total_pages_for_caption}쪽)")

with side_col, st.container(key="side_sticky"):
    # 카테고리별 수집 현황 - 부정/중립/긍정 비율을 한 줄 막대로 표시 (스크롤해도 화면에 붙어 따라오도록 고정)
    kw_html = ["<div class='sidebar-panel'><div class='panel-title'>카테고리별 수집 현황</div>"]
    for cat, cat_total in daily_category_counts.items():
        cat_rows = daily_df[daily_df['분야'] == cat]
        neg = int((cat_rows['논조'] == '부정').sum())
        neu = int((cat_rows['논조'] == '중립').sum())
        pos = int((cat_rows['논조'] == '긍정').sum())
        total_s = neg + neu + pos
        if total_s == 0:
            neg_pct = neu_pct = pos_pct = 0
        else:
            neg_pct = round(neg / total_s * 100)
            pos_pct = round(pos / total_s * 100)
            neu_pct = 100 - neg_pct - pos_pct  # 나머지를 중립에 배정해 항상 합계 100%가 되도록 함
        kw_html.append(
            f"<div class='kw-row'><div class='kw-label'>{cat} "
            f"<span class='kw-total'>{cat_total}건</span> / "
            f"<span class='cnt-neg'>{neg}</span> <span class='cnt-neu'>{neu}</span> <span class='cnt-pos'>{pos}</span>"
            f"</div>"
            f"<div class='sentiment-track'>"
            f"{sentiment_seg_html(neg_pct, 'sentiment-neg')}"
            f"{sentiment_seg_html(neu_pct, 'sentiment-neu')}"
            f"{sentiment_seg_html(pos_pct, 'sentiment-pos')}"
            f"</div></div>"
        )
    kw_html.append("</div>")
    st.markdown("".join(kw_html), unsafe_allow_html=True)

# 표시 개수 선택 + 페이지 번호 버튼은 카테고리별 수집 현황 밑, 사이드바에 배치 (폭도 그 칸과 동일)
with side_col, st.container(key="side_pagination"):
    if issue_groups:
        page_size = st.selectbox("표시 개수", options=page_size_options, index=0, label_visibility="collapsed", key="pg_size_select")

        total_pages = max(1, -(-total_count // page_size))  # 올림 나눗셈
        if st.session_state.list_page > total_pages:
            st.session_state.list_page = 1
        page = st.session_state.list_page

        WINDOW = 5
        start_p = max(1, min(page - WINDOW // 2, total_pages - WINDOW + 1))
        end_p = min(total_pages, start_p + WINDOW - 1)
        page_numbers = list(range(start_p, end_p + 1))

        pg_cols = st.columns(2 + len(page_numbers))
        with pg_cols[0]:
            if st.button("◀", key="page_prev_btn", disabled=(page <= 1), use_container_width=True):
                st.session_state.list_page = max(1, page - 1)
                st.rerun()
        for i, p in enumerate(page_numbers):
            with pg_cols[1 + i]:
                if st.button(str(p), key=f"page_btn_{p}", type=("primary" if p == page else "secondary"), use_container_width=True):
                    st.session_state.list_page = p
                    st.rerun()
        with pg_cols[1 + len(page_numbers)]:
            if st.button("▶", key="page_next_btn", disabled=(page >= total_pages), use_container_width=True):
                st.session_state.list_page = min(total_pages, page + 1)
                st.rerun()
    else:
        page_size, page, total_pages = page_size_options[0], 1, 1

with main_col:
    if not issue_groups:
        st.info("조건에 맞는 기사가 없습니다.")
    else:
        start = (page - 1) * page_size
        issue_groups = issue_groups[start:start + page_size]

    with st.container(key="article_list_scroll"):
        for g in issue_groups:
            bc = badge_class(g['sentiment'])
            reasons = selection_reasons(g)
            fine_tag = extract_fine_amount(f"{g['title']} {g['summary']}")
            domain = extract_domain(g['rep_link'])
            # 실제 발행 시각이 있으면 우선 표시, 없으면(구버전 데이터) 수집 시각으로 대체
            card_dt = g['rep_pub_dt'] if pd.notna(g['rep_pub_dt']) else g['rep_dt']
            dt_display = card_dt.strftime('%m.%d %H:%M') if pd.notna(card_dt) else ''
    
            tags_html = f"<span class='{bc}'>{g['sentiment']}</span> <span class='chip-category'>{g['category']}</span>"
            if '제재·심결 신호' in reasons:
                tags_html += " <span class='chip-tag-warn'>제재·규제</span>"
            if fine_tag:
                tags_html += f" <span class='chip-alert'>{fine_tag}</span>"
    
            toggle_key = f"show_related_{g['title']}_{g['rep_link']}"
            is_open = st.session_state.get(toggle_key, False)
    
            with st.container(border=True, key=f"article_card_{toggle_key}"):
                st.markdown(
                    f"<div>{tags_html} <strong><a href='{g['rep_link']}' target='_blank' style='color:inherit; text-decoration:none;'>{g['title']}</a></strong> "
                    f"<span style='color:#b8860b; font-size:0.85em;'>중요도 {g['importance']}/10</span> "
                    f"<span class='card-meta-inline'>{dt_display} · {domain} · 총 보도 매체 {g['press_count']}개</span></div>",
                    unsafe_allow_html=True,
                )
                st.markdown(f"<a href='{g['rep_link']}' target='_blank' style='text-decoration:none; color:inherit; display:block;'><div class='summary-box-blue'>{g['summary']}</div></a>", unsafe_allow_html=True)
    
                with st.container(key=f"toggle_row_{toggle_key}"):
                    toggle_col, reason_col = st.columns([1, 3])
                    with toggle_col:
                        arrow = "▲" if is_open else "▼"
                        if st.button(f"유사 기사 {g['press_count']}건 {arrow}", key=f"toggle_btn_{toggle_key}"):
                            st.session_state[toggle_key] = not is_open
                            st.rerun()
                    with reason_col:
                        if reasons:
                            reason_chips = " ".join(f"<span class='reason-chip'>{r}</span>" for r in reasons)
                            st.markdown(f"<div class='reason-box'>선별 근거 {reason_chips}</div>", unsafe_allow_html=True)
    
                if is_open:
                    for _, row in g['rows'].iterrows():
                        st.markdown(f"- [{row['언론사']}] <a href='{row['기사링크']}' target='_blank' style='text-decoration:none; color:#1565c0;'>{row['제목']}</a>", unsafe_allow_html=True)
