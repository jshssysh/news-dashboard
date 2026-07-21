import streamlit as st
import pandas as pd
from datetime import datetime, timedelta

# 페이지 기본 설정
st.set_page_config(page_title="Daily Brief", layout="centered", initial_sidebar_state="collapsed")

# 1. 안정적인 UI 최적화 CSS (오직 날짜 네비게이션만 타겟팅)
st.markdown("""
<style>
/* 1. 날짜 선택기(첫 번째 stHorizontalBlock) 강제 1줄 고정 */
div[data-testid="stVerticalBlock"] > div > div[data-testid="stHorizontalBlock"]:first-of-type {
    flex-wrap: nowrap !important;
    align-items: center !important;
}
div[data-testid="stVerticalBlock"] > div > div[data-testid="stHorizontalBlock"]:first-of-type > div[data-testid="column"] {
    width: 33% !important;
    min-width: 0 !important;
    flex: 1 1 0% !important;
}

/* 2. 요약 박스 디자인 */
.summary-box {
    background-color: #2D2D2D;
    padding: 15px;
    border-radius: 8px;
    margin-bottom: 10px;
    font-size: 0.95em;
    line-height: 1.5;
    color: #E0E0E0;
}

/* 3. 라디오 버튼(카테고리)을 반응형 칩(Chip) 형태로 디자인 변경 */
div[role="radiogroup"] {
    gap: 0.5rem;
}
div[role="radiogroup"] > label {
    background-color: #1E1E1E;
    padding: 5px 15px;
    border-radius: 20px;
    border: 1px solid #444;
}
</style>
""", unsafe_allow_html=True)

# 2. 데이터 로드 함수
@st.cache_data(ttl=600)
def load_data():
    try:
        df = pd.read_csv("news_list.csv")
        df['dt'] = pd.to_datetime(df['수집일자'], errors='coerce')
        df['date_str'] = df['dt'].dt.strftime('%Y/%m/%d')
        return df
    except Exception:
        return pd.DataFrame()

df = load_data()

# 3. 날짜 상태 관리
if 'current_date' not in st.session_state:
    if not df.empty:
        st.session_state.current_date = df['dt'].max().date()
    else:
        st.session_state.current_date = datetime.now().date()

# 4. 상단 헤더
st.title("D Daily Brief")
st.caption(f"{st.session_state.current_date.strftime('%Y년 %m월 %d일')} 발행")

# 5. 날짜 네비게이션 UI (정밀 CSS로 모바일 1줄 고정됨)
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

# 6. 데이터 필터링
if not df.empty:
    target_date_str = st.session_state.current_date.strftime('%Y/%m/%d')
    daily_df = df[df['date_str'] == target_date_str]
else:
    daily_df = pd.DataFrame()

# 7. 메트릭 대시보드 (기본 디자인 복구로 찌그러짐 방지)
if not daily_df.empty:
    m_col1, m_col2 = st.columns(2)
    with m_col1:
        st.metric("수집 기사", f"{len(daily_df)}건")
        st.metric("중립 보도", f"{len(daily_df[daily_df['논조'] == '중립'])}건")
    with m_col2:
        st.metric("긍정 신호", f"{len(daily_df[daily_df['논조'] == '긍정'])}건")
        st.metric("부정 신호", f"{len(daily_df[daily_df['논조'] == '부정'])}건")
else:
    st.info("해당 날짜에 수집된 기사 데이터가 없습니다.")

st.divider()

# 8. 카테고리 필터 (반응형 라디오 버튼 적용)
if not daily_df.empty:
    categories = ['전체'] + list(daily_df['분야'].unique())
    
    # st.columns 대신 자동 줄바꿈이 지원되는 라디오 위젯 사용
    selected_category = st.radio(
        "카테고리 선택",
        options=categories,
        horizontal=True,
        label_visibility="collapsed"
    )
    
    st.write("") 
    
    if selected_category == '전체':
        filtered_df = daily_df
    else:
        filtered_df = daily_df[daily_df['분야'] == selected_category]

    # 9. 기사 리스트 출력 (아코디언)
    grouped = filtered_df.groupby('대표이슈')
    
    for group_title, group_data in grouped:
        rep_article = group_data.iloc[0]
        sentiment_color = "#4CAF50" if rep_article['논조'] == '긍정' else "#F44336" if rep_article['논조'] == '부정' else "#FFC107"
        
        with st.expander(f"{group_title} ({len(group_data)}건)"):
            st.markdown(f"<span style='color:{sentiment_color}; font-weight:bold;'>[{rep_article['논조']}]</span> <span style='color:#888;'>{rep_article['분야']}</span>", unsafe_allow_html=True)
            st.markdown(f"<div class='summary-box'>{rep_article['AI요약']}</div>", unsafe_allow_html=True)
            for _, row in group_data.iterrows():
                st.markdown(f"- [{row['언론사']}] <a href='{row['기사링크']}' target='_blank' style='text-decoration:none; color:#4DA8DA;'>{row['제목']}</a>", unsafe_allow_html=True)
