import streamlit as st
import pandas as pd
from datetime import datetime, timedelta

st.set_page_config(page_title="Daily Brief", layout="centered", initial_sidebar_state="collapsed")

st.markdown("""
<style>
div[data-testid="stVerticalBlock"] > div > div[data-testid="stHorizontalBlock"]:first-of-type {
    flex-wrap: nowrap !important;
    align-items: center !important;
}
div[data-testid="stVerticalBlock"] > div > div[data-testid="stHorizontalBlock"]:first-of-type > div[data-testid="column"] {
    width: 33% !important;
    min-width: 0 !important;
    flex: 1 1 0% !important;
}
.summary-box {
    background-color: #2D2D2D;
    padding: 15px;
    border-radius: 8px;
    margin-bottom: 10px;
    font-size: 0.95em;
    line-height: 1.5;
    color: #E0E0E0;
}
div[role="radiogroup"] { gap: 0.5rem; }
div[role="radiogroup"] > label {
    background-color: #1E1E1E;
    padding: 5px 15px;
    border-radius: 20px;
    border: 1px solid #444;
}
</style>
""", unsafe_allow_html=True)

@st.cache_data(ttl=600)
def load_data():
    try:
        df = pd.read_csv("news_list.csv")
        df['dt'] = pd.to_datetime(df['수집일자'], errors='coerce')
        df['date_str'] = df['dt'].dt.strftime('%Y/%m/%d')
        return df
    except Exception: return pd.DataFrame()

df = load_data()

if 'current_date' not in st.session_state:
    if not df.empty: st.session_state.current_date = df['dt'].max().date()
    else: st.session_state.current_date = datetime.now().date()

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

if not df.empty:
    daily_df = df[df['date_str'] == st.session_state.current_date.strftime('%Y/%m/%d')]
else:
    daily_df = pd.DataFrame()

if not daily_df.empty:
    m_col1, m_col2, m_col3 = st.columns(3)
    with m_col1:
        st.metric("수집 기사", f"{len(daily_df)}건")
        st.metric("중립 보도", f"{len(daily_df[daily_df['논조'] == '중립'])}건")
    with m_col2:
        st.metric("긍정 신호", f"{len(daily_df[daily_df['논조'] == '긍정'])}건")
        st.metric("부정 신호", f"{len(daily_df[daily_df['논조'] == '부정'])}건")
    with m_col3:
        st.metric("판단 실패", f"{len(daily_df[daily_df['논조'] == '판단 실패'])}건")
else:
    st.info("해당 날짜에 수집된 기사 데이터가 없습니다.")

st.divider()

if not daily_df.empty:
    categories = ['전체'] + list(daily_df['분야'].unique())
    selected_category = st.radio("카테고리 선택", options=categories, horizontal=True, label_visibility="collapsed")
    st.write("") 
    
    filtered_df = daily_df if selected_category == '전체' else daily_df[daily_df['분야'] == selected_category]
    grouped = filtered_df.groupby('대표이슈')
    
    for group_title, group_data in grouped:
        rep_article = group_data.iloc[0]
        # 에러 시각화를 위해 판단 실패 항목은 보라색 표기
        sentiment_color = "#4CAF50" if rep_article['논조'] == '긍정' else "#F44336" if rep_article['논조'] == '부정' else "#9C27B0" if rep_article['논조'] == '판단 실패' else "#FFC107"
        
        with st.expander(f"{group_title} ({len(group_data)}건)"):
            st.markdown(f"<span style='color:{sentiment_color}; font-weight:bold;'>[{rep_article['논조']}]</span> <span style='color:#888;'>{rep_article['분야']}</span>", unsafe_allow_html=True)
            st.markdown(f"<div class='summary-box'>{rep_article['AI요약']}</div>", unsafe_allow_html=True)
            for _, row in group_data.iterrows():
                st.markdown(f"- [{row['언론사']}] <a href='{row['기사링크']}' target='_blank' style='text-decoration:none; color:#4DA8DA;'>{row['제목']}</a>", unsafe_allow_html=True)
