import streamlit as st
import pandas as pd
from datetime import datetime, timedelta

st.set_page_config(page_title="Daily Brief", layout="centered", initial_sidebar_state="collapsed")

# 모바일 레이아웃 고정 및 기존 커스텀 UI 복원 CSS 융합
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
div[role="radiogroup"] { gap: 0.5rem; }
div[role="radiogroup"] > label {
    background-color: #1E1E1E; padding: 5px 15px; border-radius: 20px; border: 1px solid #444;
}
/* 기존 스크린샷 UI 완벽 복원 (배지 및 남색 요약 박스) */
.badge-positive { border: 1px solid #4CAF50; color: #4CAF50; padding: 2px 8px; border-radius: 4px; font-weight: bold; font-size: 0.9em; margin-right: 8px;}
.badge-neutral { border: 1px solid #FFC107; color: #FFC107; padding: 2px 8px; border-radius: 4px; font-weight: bold; font-size: 0.9em; margin-right: 8px;}
.badge-negative { border: 1px solid #F44336; color: #F44336; padding: 2px 8px; border-radius: 4px; font-weight: bold; font-size: 0.9em; margin-right: 8px;}
.badge-fail { border: 1px solid #9C27B0; color: #9C27B0; padding: 2px 8px; border-radius: 4px; font-weight: bold; font-size: 0.9em; margin-right: 8px;}

.summary-box-blue { 
    background-color: #0d1e36; /* 남색 배경 복원 */
    padding: 15px; 
    border-radius: 8px; 
    margin-bottom: 10px; 
    font-size: 0.95em; 
    color: #4DA8DA; /* 밝은 파란색 텍스트 복원 */
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
        sentiment = rep_article['논조']
        
        # 논조에 따른 배지 클래스 할당
        if sentiment == '긍정': badge_class = "badge-positive"
        elif sentiment == '부정': badge_class = "badge-negative"
        elif sentiment == '판단 실패': badge_class = "badge-fail"
        else: badge_class = "badge-neutral"
        
        main_press = rep_article['언론사']
        total_press = len(group_data)
        
        st.markdown(f"<div><span class='{badge_class}'>{sentiment}</span> <strong>{group_title} 🔗</strong></div>", unsafe_allow_html=True)
        st.markdown(f"<div style='margin-top:5px; margin-bottom:15px; font-size:0.85em; color:#bbb;'>분야: <span style='color:#4CAF50;'>{rep_article['분야']}</span> | 메인 언론사: <span style='color:#4CAF50;'>{main_press}</span> | 총 보도 매체: <span style='color:#4CAF50;'>{total_press}개 언론사</span> | 논조 분포: <span class='{badge_class}' style='padding:0px 4px; font-size:1em; font-weight:normal;'>{sentiment} {total_press}</span></div>", unsafe_allow_html=True)
        
        st.markdown(f"<div class='summary-box-blue'>💡 AI 핵심 요약: {rep_article['AI요약']}</div>", unsafe_allow_html=True)
        
        with st.expander(f"📁 언론사별 반응 및 관련 기사 보기 ({total_press}개 보도 기사 펼치기)"):
            for _, row in group_data.iterrows():
                st.markdown(f"- [{row['언론사']}] <a href='{row['기사링크']}' target='_blank' style='text-decoration:none; color:#4DA8DA;'>{row['제목']}</a>", unsafe_allow_html=True)
        
        st.write("---")
