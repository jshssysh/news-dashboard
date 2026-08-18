import streamlit as st
import pandas as pd
from datetime import datetime, timedelta

st.set_page_config(page_title="Daily Brief", layout="centered", initial_sidebar_state="collapsed")

# 모바일 레이아웃 고정 및 커스텀 UI CSS
st.markdown("""
<style>
/* 날짜 네비게이션 1줄 고정 */
div[data-testid="stVerticalBlock"] > div > div[data-testid="stHorizontalBlock"]:first-of-type {
    flex-wrap: nowrap !important;
    align-items: center !important;
}
div[data-testid="stVerticalBlock"] > div > div[data-testid="stHorizontalBlock"]:first-of-type > div[data-testid="column"] {
    width: 33% !important; min-width: 0 !important; flex: 1 1 0% !important;
}
/* 카테고리 라디오 버튼 반응형 칩 형태 */
div[role="radiogroup"] { gap: 0.5rem; flex-wrap: wrap; }
div[role="radiogroup"] > label {
    background-color: #f0f2f5; padding: 5px 15px; border-radius: 20px; border: 1px solid #dfe2e7; color: #333;
}
/* 배지 및 요약 박스 (라이트 테마) */
.badge-positive { background-color: #e8f5e9; color: #2e7d32; padding: 2px 8px; border-radius: 4px; font-weight: bold; font-size: 0.85em; margin-right: 8px;}
.badge-neutral { background-color: #fff3cd; color: #8a6100; padding: 2px 8px; border-radius: 4px; font-weight: bold; font-size: 0.85em; margin-right: 8px;}
.badge-negative { background-color: #fdecea; color: #c62828; padding: 2px 8px; border-radius: 4px; font-weight: bold; font-size: 0.85em; margin-right: 8px;}
.badge-fail { background-color: #f3e5f5; color: #7b1fa2; padding: 2px 8px; border-radius: 4px; font-weight: bold; font-size: 0.85em; margin-right: 8px;}

.summary-box-blue {
    background-color: #0d1e36;
    padding: 15px;
    border-radius: 8px;
    margin-bottom: 10px;
    font-size: 0.95em;
    color: #8ab4f8;
}

/* 통계 타일 */
.stat-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin-bottom: 8px; }
.stat-card { background-color: #f5f6f8; border: 1px solid #e2e4e8; border-radius: 10px; padding: 12px; }
.stat-card .label { font-size: 0.8em; color: #6b7280; margin-bottom: 4px; }
.stat-card .value { font-size: 1.6em; font-weight: bold; color: #1e3a5f; }

/* 오늘의 신호 (라이트 페이지 위 짙은 네이비 강조 카드) */
.signal-box { background-color: #12203a; border: 1px solid #12203a; border-radius: 10px; padding: 16px; margin-bottom: 15px; }
.signal-tag { display: inline-block; background-color: #24406e; color: #8ab4f8; font-size: 0.75em; padding: 2px 8px; border-radius: 4px; }
.signal-title { font-size: 1.1em; font-weight: bold; margin: 8px 0 6px 0; color: #fff; }
.signal-body { font-size: 0.9em; color: #cbd5e1; line-height: 1.5; }

/* 카테고리별 주요뉴스 목록 */
.sidebar-item { display: flex; justify-content: space-between; gap: 8px; padding: 8px 0; border-bottom: 1px solid #eaecef; font-size: 0.9em; }
.sidebar-item .cat { color: #1e3a5f; font-weight: bold; white-space: nowrap; }
.sidebar-item .issue { color: #444; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sidebar-item .score { color: #b8860b; font-size: 0.85em; white-space: nowrap; }
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
            'rows': gdf_sorted,
        })
    groups.sort(key=lambda g: g['importance'], reverse=True)
    return groups


def badge_class(sentiment):
    return {'긍정': 'badge-positive', '부정': 'badge-negative', '판단 실패': 'badge-fail'}.get(sentiment, 'badge-neutral')


df = load_data()

if 'current_date' not in st.session_state:
    st.session_state.current_date = df['dt'].max().date() if not df.empty else datetime.now().date()

st.title("D Daily Brief")
st.caption(f"{st.session_state.current_date.strftime('%Y년 %m월 %d일')} 발행")

col1, col2, col3 = st.columns([1, 3, 1])
with col1:
    if st.button("◀", use_container_width=True):
        st.session_state.current_date -= timedelta(days=1)
        st.rerun()
with col2:
    date_display = st.session_state.current_date.strftime('%Y/%m/%d')
    st.markdown(f"<div style='text-align: center; font-weight: bold; font-size: 1.2em; padding-top: 5px;'>{date_display}</div>", unsafe_allow_html=True)
with col3:
    if st.button("▶", use_container_width=True):
        st.session_state.current_date += timedelta(days=1)
        st.rerun()

st.divider()

daily_df = df[df['date_str'] == st.session_state.current_date.strftime('%Y/%m/%d')] if not df.empty else pd.DataFrame()

if daily_df.empty:
    st.info("해당 날짜에 수집된 기사 데이터가 없습니다.")
    st.stop()

all_issue_groups = build_issue_groups(daily_df)
major_count = int((daily_df['중요도'] >= 6).sum())
spread_count = len([g for g in all_issue_groups if g['press_count'] >= 5])
sentiment_all_counts = daily_df['논조'].value_counts()

st.markdown(f"""
<div class="stat-grid">
    <div class="stat-card"><div class="label">수집 기사</div><div class="value">{len(daily_df)}건</div></div>
    <div class="stat-card"><div class="label">주요 뉴스</div><div class="value">{major_count}건</div></div>
    <div class="stat-card"><div class="label">보도 확산</div><div class="value">{spread_count}건</div></div>
</div>
""", unsafe_allow_html=True)
st.caption(
    f"논조 분포 · 긍정 {sentiment_all_counts.get('긍정', 0)} · 중립 {sentiment_all_counts.get('중립', 0)} · "
    f"부정 {sentiment_all_counts.get('부정', 0)} · 판단실패 {sentiment_all_counts.get('판단 실패', 0)}"
)

# 오늘의 신호 (규칙 기반 요약 — 별도 AI 호출 없이 수집된 데이터로 자동 생성)
top_issue = all_issue_groups[0]
scored_sentiments = daily_df[daily_df['중요도'] >= 5]['논조'].value_counts()
dominant_sentiment = scored_sentiments.idxmax() if not scored_sentiments.empty else '중립'
headline_map = {'부정': '제재·리스크 신호 부각', '긍정': '호재 신호 부각', '중립': '중립 흐름 지속', '판단 실패': '판단 보류 이슈 다수'}
headline = headline_map.get(dominant_sentiment, '주요 흐름 점검')

st.markdown(f"""
<div class="signal-box">
    <span class="signal-tag">규칙 요약</span>
    <div class="signal-title">오늘 주요 흐름 · {headline}</div>
    <div class="signal-body">오늘 수집된 {len(daily_df)}건 중 <b>{top_issue['category']}</b> 분야에서 가장 주목되는 신호가 발생했습니다.
    중요도가 가장 높은 이슈는 '<b>{top_issue['title']}</b>'(중요도 {top_issue['importance']}/10)이며, 관련 보도가 {top_issue['press_count']}건입니다.</div>
</div>
""", unsafe_allow_html=True)

st.divider()

# 검색 및 필터
f_col1, f_col2, f_col3 = st.columns([2, 1, 1])
with f_col1:
    search_text = st.text_input("검색", placeholder="제목, 요약에서 검색", label_visibility="collapsed")
with f_col2:
    sentiment_filter = st.selectbox("논조", options=['논조 전체', '긍정', '중립', '부정', '판단 실패'], label_visibility="collapsed")
with f_col3:
    sort_option = st.selectbox("정렬", options=['중요도순', '최신순'], label_visibility="collapsed")

# 카테고리 칩 (건수 표시)
category_counts = daily_df['분야'].value_counts()
categories = ['전체'] + list(category_counts.index)
chip_labels = [f"전체 {len(daily_df)}"] + [f"{c} {category_counts[c]}" for c in category_counts.index]
selected_idx = st.radio(
    "카테고리 선택", options=range(len(categories)),
    format_func=lambda i: chip_labels[i], horizontal=True, label_visibility="collapsed",
)
selected_category = categories[selected_idx]

st.write("")

# 필터 적용
filtered_df = daily_df if selected_category == '전체' else daily_df[daily_df['분야'] == selected_category]
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
if sort_option == '최신순':
    issue_groups.sort(key=lambda g: g['rows']['dt'].max(), reverse=True)

if not issue_groups:
    st.info("조건에 맞는 기사가 없습니다.")

for g in issue_groups:
    bc = badge_class(g['sentiment'])
    with st.container(border=True):
        st.markdown(f"<div><span class='{bc}'>{g['sentiment']}</span> <strong>{g['title']} 🔗</strong> <span style='color:#b8860b; font-size:0.85em;'>중요도 {g['importance']}/10</span></div>", unsafe_allow_html=True)
        st.markdown(f"<div style='margin-top:5px; margin-bottom:15px; font-size:0.85em; color:#6b7280;'>분야: <span style='color:#1e3a5f; font-weight:bold;'>{g['category']}</span> | 메인 언론사: <span style='color:#1e3a5f; font-weight:bold;'>{g['main_press']}</span> | 총 보도 매체: <span style='color:#1e3a5f; font-weight:bold;'>{g['press_count']}개 언론사</span> | 논조 분포: <span class='{bc}' style='padding:0px 4px; font-size:1em; font-weight:normal;'>{g['sentiment']} {g['press_count']}</span></div>", unsafe_allow_html=True)
        st.markdown(f"<div class='summary-box-blue'>💡 AI 핵심 요약: {g['summary']}</div>", unsafe_allow_html=True)
        with st.expander(f"📁 언론사별 반응 및 관련 기사 보기 ({g['press_count']}개 보도 기사 펼치기)"):
            for _, row in g['rows'].iterrows():
                st.markdown(f"- [{row['언론사']}] <a href='{row['기사링크']}' target='_blank' style='text-decoration:none; color:#1565c0;'>{row['제목']}</a>", unsafe_allow_html=True)

# 카테고리별 주요뉴스 (데스크톱 사이드바를 모바일 우선 구조에 맞춰 접이식 섹션으로 대체)
with st.expander("📌 카테고리별 주요뉴스 보기"):
    seen_cats = set()
    for g in all_issue_groups:
        if g['category'] in seen_cats:
            continue
        seen_cats.add(g['category'])
        st.markdown(
            f"<div class='sidebar-item'><span class='cat'>{g['category']}</span>"
            f"<span class='issue'>{g['title']}</span><span class='score'>중요도 {g['importance']}</span></div>",
            unsafe_allow_html=True,
        )
