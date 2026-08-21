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
        reasons.append(f"'{kw}' 키워드 {count}회 반복")
    if g["category"] == "제재·심결":
        reasons.append("제재·심결 신호")
    return reasons

st.set_page_config(page_title="Daily Brief", layout="wide", initial_sidebar_state="collapsed")

# 색상은 Streamlit 테마 변수(var(--...))를 사용해 라이트/다크(시스템 설정) 전환에 자동으로 맞춘다
st.markdown("""
<style>
.block-container { max-width: 1100px; padding-top: 3rem; }

.app-header { display: flex; align-items: center; gap: 9px; padding: 2px 0; }
.app-logo { background: linear-gradient(135deg, var(--primary-color), #ff8a65); color: #fff; font-weight: 800; width: 26px; height: 26px; border-radius: 7px; display: flex; align-items: center; justify-content: center; font-size: 0.85em; flex-shrink: 0; box-shadow: 0 2px 6px rgba(255,75,75,0.35); }
.app-title { font-size: 1.2em; font-weight: 800; letter-spacing: -0.2px; color: var(--text-color); }

/* 상단 바(제목+탭+날짜) 한 줄: 좁은 화면에서도 줄바꿈 없이 유지, 버튼/텍스트는 작게
   컬럼 비율(st.columns) 대신 내용 크기에 맞춰 붙여 배치하고, 날짜 네비게이션만 오른쪽 끝으로 밀어냄 */
.st-key-top_bar div[data-testid="stHorizontalBlock"] { display: flex !important; flex-wrap: nowrap !important; align-items: center !important; gap: 10px !important; }
.st-key-top_bar div[data-testid="column"] { min-width: 0 !important; width: auto !important; flex: 0 0 auto !important; }
.st-key-top_bar div[data-testid="column"]:nth-of-type(5) { margin-left: auto !important; }
.st-key-top_bar button { padding: 4px 14px !important; font-size: 0.75em !important; white-space: nowrap; border-radius: 20px !important; }

/* 카테고리별 수집 현황 패널: 본문을 스크롤해도 화면에 붙어서 따라옴
   (Streamlit이 컬럼/블록에 자체적으로 overflow를 걸어두면 sticky가 무효화되므로 명시적으로 풀어줌) */
div[data-testid="stHorizontalBlock"]:has(.st-key-side_sticky),
div[data-testid="stHorizontalBlock"]:has(.st-key-side_sticky) > div[data-testid="column"],
div[data-testid="stHorizontalBlock"]:has(.st-key-side_sticky) div[data-testid="stVerticalBlock"] {
    overflow: visible !important;
}
.st-key-side_sticky { position: sticky !important; top: 20px; align-self: flex-start; z-index: 1; }
.app-header { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

/* 카테고리 라디오 버튼: 개수가 많아도 줄바꿈 없이 한 줄, 넘치면 가로 스크롤 */
div[role="radiogroup"] { gap: 0.5rem; flex-wrap: nowrap; overflow-x: auto; padding-bottom: 6px; }
div[role="radiogroup"] > label {
    background-color: var(--secondary-background-color); padding: 5px 15px; border-radius: 20px;
    border: 1px solid rgba(128, 128, 128, 0.3); color: var(--text-color);
    white-space: nowrap; flex-shrink: 0;
}

/* 배지 및 칩 (라이트/다크 공통 — 배지 자체는 항상 옅은 색 배경 + 짙은 텍스트라 어느 테마에서도 읽힘) */
.badge-positive { background-color: #e8f5e9; color: #2e7d32; padding: 2px 8px; border-radius: 4px; font-weight: bold; font-size: 0.85em; margin-right: 8px;}
.badge-neutral { background-color: #fff3cd; color: #8a6100; padding: 2px 8px; border-radius: 4px; font-weight: bold; font-size: 0.85em; margin-right: 8px;}
.badge-negative { background-color: #fdecea; color: #c62828; padding: 2px 8px; border-radius: 4px; font-weight: bold; font-size: 0.85em; margin-right: 8px;}
.badge-fail { background-color: #f3e5f5; color: #7b1fa2; padding: 2px 8px; border-radius: 4px; font-weight: bold; font-size: 0.85em; margin-right: 8px;}
.chip-category { background-color: var(--secondary-background-color); color: var(--text-color); padding: 2px 8px; border-radius: 4px; font-weight: bold; font-size: 0.85em; margin-right: 8px; border: 1px solid rgba(128,128,128,0.3); }
.chip-tag-warn { background-color: #fdecea; color: #c62828; padding: 2px 8px; border-radius: 4px; font-weight: bold; font-size: 0.85em; margin-right: 8px; }
.chip-alert { background-color: #c62828; color: #fff; padding: 2px 8px; border-radius: 4px; font-weight: bold; font-size: 0.8em; margin-right: 8px; }
.card-meta { font-size: 0.8em; opacity: 0.6; margin: 4px 0 10px 0; }
.reason-chip { display: inline-block; background-color: var(--secondary-background-color); color: var(--text-color); opacity: 0.85; padding: 1px 7px; border-radius: 4px; font-size: 0.78em; margin-right: 4px; border: 1px solid rgba(128,128,128,0.25); }
.stButton button { white-space: nowrap; }

/* AI 요약 / 오늘 주요 내용은 항상 짙은 네이비 강조 카드로 고정 (테마와 무관한 브랜드 악센트) */
.summary-box-blue { background-color: #0d1e36; padding: 15px; border-radius: 8px; margin-bottom: 10px; font-size: 0.95em; color: #8ab4f8; }
.signal-box { background-color: #12203a; border: 1px solid #12203a; border-radius: 10px; padding: 16px; margin-bottom: 15px; }
.signal-tag { display: inline-block; background-color: #24406e; color: #8ab4f8; font-size: 0.75em; padding: 2px 8px; border-radius: 4px; }
.signal-item { margin-top: 12px; padding-top: 12px; border-top: 1px solid rgba(255,255,255,0.15); }
.signal-item:first-of-type { margin-top: 10px; padding-top: 0; border-top: none; }
.signal-num { display: inline-block; background-color: #8ab4f8; color: #0d1e36; font-weight: bold; width: 18px; height: 18px; border-radius: 50%; text-align: center; font-size: 0.75em; line-height: 18px; margin-right: 4px; }
.signal-item-head { color: #fff; font-size: 0.95em; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.signal-item-body { color: #cbd5e1; font-size: 0.85em; margin-top: 4px; line-height: 1.4; }
.chip-category-dark { background-color: #24406e; color: #8ab4f8; padding: 1px 6px; border-radius: 4px; font-size: 0.8em; margin: 0 4px; }
.signal-score { color: #f6c453; font-size: 0.8em; margin-left: 6px; }
.reason-chip-dark { display: inline-block; background-color: #24406e; color: #8ab4f8; opacity: 0.9; padding: 1px 7px; border-radius: 4px; font-size: 0.75em; margin-right: 4px; margin-top: 6px; }

/* 통계 타일 */
.stat-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin-bottom: 8px; }
.stat-card { background-color: var(--secondary-background-color); border: 1px solid rgba(128,128,128,0.3); border-radius: 10px; padding: 12px; }
.stat-card .label { font-size: 0.8em; color: var(--text-color); opacity: 0.65; margin-bottom: 4px; }
.stat-card .value { font-size: 1.6em; font-weight: bold; color: var(--text-color); }
.stat-card .sub { font-size: 0.72em; color: var(--text-color); opacity: 0.6; margin-top: 4px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

/* 카테고리별 주요뉴스 (창 크기에 맞춰 박스가 줄어들며 항상 한 줄 유지) */
.cat-news-grid { display: flex; flex-wrap: nowrap; gap: 8px; margin-bottom: 15px; overflow-x: auto; }
.cat-news-card { background-color: var(--secondary-background-color); border: 1px solid rgba(128,128,128,0.3); border-radius: 10px; padding: 10px; flex: 1 1 0; min-width: 70px; }
.cat-news-card .cat-name { color: var(--primary-color); font-weight: bold; font-size: 0.8em; margin-bottom: 4px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.cat-news-card .cat-issue { color: var(--text-color); font-size: 0.85em; display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; margin-bottom: 4px; }
.cat-news-card .cat-score { color: var(--text-color); opacity: 0.6; font-size: 0.75em; white-space: nowrap; }

/* 사이드바 공통 패널 */
.sidebar-panel { background-color: var(--secondary-background-color); border: 1px solid rgba(128,128,128,0.3); border-radius: 10px; padding: 14px; margin-bottom: 14px; }
.sidebar-panel .panel-title { font-weight: bold; color: var(--text-color); margin-bottom: 8px; }

.kw-row { margin-bottom: 10px; }
.kw-row .kw-label { display: flex; justify-content: space-between; font-size: 0.85em; color: var(--text-color); font-weight: bold; margin-bottom: 4px; }
.kw-row .kw-count { opacity: 0.65; font-weight: normal; }
.kw-track { background-color: rgba(128,128,128,0.25); border-radius: 6px; height: 6px; overflow: hidden; }
.kw-fill { background-color: var(--primary-color); height: 100%; border-radius: 6px; }
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

with st.container(key="top_bar"):
    title_col, tab_news, tab_law, tab_org, d_prev, d_text, d_next = st.columns([2.4, 0.8, 0.8, 1.2, 0.4, 1.1, 0.4])

    with title_col:
        st.markdown("<div class='app-header'><span class='app-logo'>D</span><span class='app-title'>Daily Brief</span></div>", unsafe_allow_html=True)

    for label, col in [('뉴스', tab_news), ('입법', tab_law), ('공정위 조직', tab_org)]:
        with col:
            is_active = st.session_state.active_tab == label
            if st.button(label, key=f"tab_{label}", type="primary" if is_active else "secondary"):
                st.session_state.active_tab = label

    with d_prev:
        if st.button("◀", key="date_prev", use_container_width=True):
            st.session_state.current_date -= timedelta(days=1)
            st.rerun()
    with d_text:
        date_display = st.session_state.current_date.strftime('%Y/%m/%d')
        st.markdown(f"<div style='text-align: center; font-weight: 600; font-size: 0.85em; padding-top: 6px; color: var(--text-color); white-space: nowrap;'>{date_display}</div>", unsafe_allow_html=True)
    with d_next:
        if st.button("▶", key="date_next", use_container_width=True):
            st.session_state.current_date += timedelta(days=1)
            st.rerun()

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

st.divider()

daily_df = df[df['date_str'] == st.session_state.current_date.strftime('%Y/%m/%d')] if not df.empty else pd.DataFrame()

if daily_df.empty:
    st.info("해당 날짜에 수집된 기사 데이터가 없습니다.")
    st.stop()

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

st.markdown(f"""
<div class="stat-grid">
    <div class="stat-card"><div class="label">수집 기사</div><div class="value">{len(daily_df)}건</div><div class="sub">중복 {dup_count}건</div></div>
    <div class="stat-card"><div class="label">주요 뉴스</div><div class="value">{major_count}건</div><div class="sub">AI 판단 기준</div></div>
    <div class="stat-card"><div class="label">보도 확산</div><div class="value">{spread_count}건</div><div class="sub">{spread_caption} 등</div></div>
</div>
""", unsafe_allow_html=True)
caption_parts = [
    f"긍정 {sentiment_all_counts.get('긍정', 0)}",
    f"중립 {sentiment_all_counts.get('중립', 0)}",
    f"부정 {sentiment_all_counts.get('부정', 0)}",
    f"판단실패 {sentiment_all_counts.get('판단 실패', 0)}",
]
if sentiment_all_counts.get('미분석', 0):
    caption_parts.append(f"미분석 {sentiment_all_counts.get('미분석', 0)}")
st.caption("논조 분포 · " + " · ".join(caption_parts))

# 오늘 주요 내용 (규칙 기반 — 중요도 상위 3개 이슈. 별도 AI 호출 없이 수집된 데이터로 자동 생성)
top3 = all_issue_groups[:3]
signal_items = ""
for i, g in enumerate(top3, start=1):
    top3_reasons = selection_reasons(g)
    top3_reason_chips = " ".join(f"<span class='reason-chip-dark'>{r}</span>" for r in top3_reasons)
    signal_items += f"""
<div class="signal-item">
    <div class="signal-item-head"><span class="signal-num">{i}</span><span class="chip-category-dark">{g['category']}</span><a href="{g['rep_link']}" target="_blank" style="color:inherit; text-decoration:none;"><b>{g['title']}</b></a></div>
    <a href="{g['rep_link']}" target="_blank" style="text-decoration:none; color:inherit; display:block;"><div class="signal-item-body">{g['summary']} (관련 보도 {g['press_count']}건)</div></a>
    <div>{top3_reason_chips}</div>
</div>
"""

st.markdown(f"""
<div class="signal-box">
    <span class="signal-tag">오늘 주요 내용</span>
    {signal_items}
</div>
""", unsafe_allow_html=True)

# 카테고리별 주요뉴스 (본문 전체 폭에 그리드로 표시)
st.markdown("##### 카테고리별 주요뉴스")
seen_cats = set()
cat_cards = ["<div class='cat-news-grid'>"]
for g in all_issue_groups:
    if g['category'] in seen_cats:
        continue
    seen_cats.add(g['category'])
    cat_cards.append(
        f"<div class='cat-news-card'><div class='cat-name'>{g['category']}</div>"
        f"<span class='cat-issue'>{g['title']}</span><div class='cat-score'>중요도 {g['importance']}</div></div>"
    )
cat_cards.append("</div>")
st.markdown("".join(cat_cards), unsafe_allow_html=True)

st.divider()

# 데스크탑: 본문(3) + 사이드바(1) 2단 구성. 화면이 좁아지면 Streamlit이 자동으로 세로 1단으로 쌓는다.
main_col, side_col = st.columns([3, 1], gap="large")

with main_col:
    # 검색 및 필터
    f_col1, f_col2, f_col3, f_col4 = st.columns([2, 1, 1, 1])
    with f_col1:
        search_text = st.text_input("검색", placeholder="제목, 요약에서 검색", label_visibility="collapsed")
    with f_col2:
        period_choice = st.selectbox("기간", options=['오늘만', '최근 7일', '최근 30일'], label_visibility="collapsed")
    with f_col3:
        sentiment_filter = st.selectbox("논조", options=['논조 전체', '긍정', '중립', '부정', '판단 실패'], label_visibility="collapsed")
    with f_col4:
        sort_option = st.selectbox("정렬", options=['중요도순', '보도량순', '최신순'], label_visibility="collapsed")

    # 기간 선택에 따라 검색 대상 범위를 넓힘 (상단 통계·오늘의 신호는 항상 선택된 날짜 하루 기준 유지)
    period_days = {'오늘만': 0, '최근 7일': 6, '최근 30일': 29}[period_choice]
    if period_days == 0:
        scoped_df = daily_df
    else:
        range_start = st.session_state.current_date - timedelta(days=period_days)
        scoped_df = df[(df['dt'].dt.date >= range_start) & (df['dt'].dt.date <= st.session_state.current_date)]

    # 카테고리 칩 (건수는 선택된 기간 기준)
    category_counts = scoped_df['분야'].value_counts()
    categories = ['전체'] + list(category_counts.index)
    chip_labels = [f"전체 {len(scoped_df)}"] + [f"{c} {category_counts[c]}" for c in category_counts.index]
    selected_idx = st.radio(
        "카테고리 선택", options=range(len(categories)),
        format_func=lambda i: chip_labels[i], horizontal=True, label_visibility="collapsed",
    )
    selected_category = categories[selected_idx]

    st.write("")

    # 필터 적용
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

    if not issue_groups:
        st.info("조건에 맞는 기사가 없습니다.")
    else:
        total_count = len(issue_groups)
        page_size_options = [10, 20, 50]
        if "list_page" not in st.session_state:
            st.session_state.list_page = 1

        # 표시 개수 선택 + 페이지 번호 버튼(◀ 1 2 3 4 5 ▶)을 한 줄에 배치
        WINDOW = 5
        pg_cols = st.columns([1] + [0.4] * (WINDOW + 2))
        with pg_cols[0]:
            page_size = st.selectbox("표시 개수", options=page_size_options, index=1, label_visibility="collapsed")

        total_pages = max(1, -(-total_count // page_size))  # 올림 나눗셈
        if st.session_state.list_page > total_pages:
            st.session_state.list_page = 1
        page = st.session_state.list_page

        start_p = max(1, min(page - WINDOW // 2, total_pages - WINDOW + 1))
        end_p = min(total_pages, start_p + WINDOW - 1)
        page_numbers = list(range(start_p, end_p + 1))

        with pg_cols[1]:
            if st.button("◀", key="page_prev_btn", disabled=(page <= 1), use_container_width=True):
                st.session_state.list_page = max(1, page - 1)
                st.rerun()
        for i, p in enumerate(page_numbers):
            with pg_cols[2 + i]:
                if st.button(str(p), key=f"page_btn_{p}", type=("primary" if p == page else "secondary"), use_container_width=True):
                    st.session_state.list_page = p
                    st.rerun()
        with pg_cols[2 + WINDOW]:
            if st.button("▶", key="page_next_btn", disabled=(page >= total_pages), use_container_width=True):
                st.session_state.list_page = min(total_pages, page + 1)
                st.rerun()

        start = (page - 1) * page_size
        st.caption(f"전체 {total_count}건 중 {start + 1}~{min(start + page_size, total_count)}건 표시 (총 {total_pages}쪽)")
        issue_groups = issue_groups[start:start + page_size]

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

        with st.container(border=True):
            st.markdown(f"<div>{tags_html} <strong><a href='{g['rep_link']}' target='_blank' style='color:inherit; text-decoration:none;'>{g['title']}</a></strong> <span style='color:#b8860b; font-size:0.85em;'>중요도 {g['importance']}/10</span></div>", unsafe_allow_html=True)
            st.markdown(f"<div class='card-meta'>{dt_display} · {domain} · 총 보도 매체 {g['press_count']}개</div>", unsafe_allow_html=True)
            st.markdown(f"<a href='{g['rep_link']}' target='_blank' style='text-decoration:none; color:inherit; display:block;'><div class='summary-box-blue'>{g['summary']}</div></a>", unsafe_allow_html=True)
            toggle_key = f"show_related_{g['title']}_{g['rep_link']}"
            is_open = st.session_state.get(toggle_key, False)

            if reasons:
                reason_chips = " ".join(f"<span class='reason-chip'>{r}</span>" for r in reasons)
                st.markdown(f"<div>선별 근거 {reason_chips}</div>", unsafe_allow_html=True)

            arrow = "▲" if is_open else "▼"
            if st.button(f"유사 기사 {g['press_count']}건 {arrow}", key=f"toggle_btn_{toggle_key}"):
                st.session_state[toggle_key] = not is_open
                st.rerun()

            if is_open:
                for _, row in g['rows'].iterrows():
                    st.markdown(f"- [{row['언론사']}] <a href='{row['기사링크']}' target='_blank' style='text-decoration:none; color:#1565c0;'>{row['제목']}</a>", unsafe_allow_html=True)

with side_col, st.container(key="side_sticky"):
    # 카테고리별 수집 현황 (오늘 기준 진행률 바) - 스크롤해도 화면에 붙어 따라오도록 고정
    max_count = int(daily_category_counts.max()) if not daily_category_counts.empty else 1
    kw_html = ["<div class='sidebar-panel'><div class='panel-title'>카테고리별 수집 현황</div>"]
    for cat, count in daily_category_counts.items():
        pct = int(count / max_count * 100) if max_count else 0
        kw_html.append(
            f"<div class='kw-row'><div class='kw-label'>{cat} <span class='kw-count'>{count}건</span></div>"
            f"<div class='kw-track'><div class='kw-fill' style='width:{pct}%;'></div></div></div>"
        )
    kw_html.append("</div>")
    st.markdown("".join(kw_html), unsafe_allow_html=True)
