import streamlit as st
import pandas as pd
from datetime import datetime, timedelta

# 페이지 기본 설정
st.set_page_config(page_title="Daily Brief", layout="centered", initial_sidebar_state="collapsed")

# 1. 날짜 네비게이션 강제 1줄 정렬 CSS 주입 (모바일 최적화)
st.markdown("""
<span id="date-nav-wrapper"></span>
<style>
/* 날짜 네비게이션 모바일 1줄 고정 */
@media (max-width: 640px) {
    #date-nav-wrapper + div[data-testid="stHorizontalBlock"] {
        flex-wrap: nowrap !important;
        align-items: center !important;
        justify-content: center !important;
    }
    #date-nav-wrapper + div[data-testid="stHorizontalBlock"] > div[data-testid="column"] {
        width: auto !important;
        min-width: 0 !important;
        flex: 1 1 auto !important;
    }
}
/* 다크모드 카드 스타일 UI */
div[data-testid="metric-container"] {
    background-color: #1E1E1E;
    border: 1px solid #333;
    padding: 5% 5% 5% 10%;
    border-radius: 10px;
}
/* 요약 텍스트 박스 */
.summary-box {
    background-color: #2D2D2D;
    padding: 15px;
    border-radius: 8px;
    margin-bottom: 10px;
    font-size: 0.95em;
    line-height: 1.5;
}
</style>
""", unsafe_allow_html=True)

# 2. 데이터 로드 함수 (캐싱)
@st.cache_data(ttl=600)
def load_data():
    try:
        # news_list.csv 파일이 같은 디렉토리에 있다고 가정
        df = pd.read_csv("news_list.csv")
        df['dt'] = pd.to_datetime(df['수집일자'], errors='coerce')
        # 시간 단위를 잘라내고 순수 날짜 문자열(YYYY/MM/DD)로 변환
        df['date_str'] = df['dt'].dt.strftime('%Y/%m/%d')
        return df
    except Exception as e:
        return pd.DataFrame()

df = load_data()

# 3. 날짜 상태 관리 (Session State)
if 'current_date' not in st.session_state:
    if not df.empty:
        # 데이터가 있으면 가장 최근 날짜를 기본값으로
        st.session_state.current_date = df['dt'].max().date()
    else:
        # 없으면 오늘 날짜
        st.session_state.current_date = datetime.now().date()

# 4. 상단 헤더
st.title("D Daily Brief")
st.caption(f"{st.session_state.current_date.strftime('%Y년 %m월 %d일')} 발행")

# 5. 날짜 네비게이션 UI (CSS 타겟팅을 위한 빈 span 태그가 바로 위에 위치)
st.markdown('<span id="date-nav-wrapper"></span>', unsafe_allow_html=True)
col1, col2, col3 = st.columns([1, 4, 1])

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

# 6. 해당 날짜 데이터 필터링
if not df.empty:
    target_date_str = st.session_state.current_date.strftime('%Y/%m/%d')
    daily_df = df[df['date_str'] == target_date_str]
else:
    daily_df = pd.DataFrame()

# 7. 메트릭 대시보드 (2x2 그리드)
if not daily_df.empty:
    total_count = len(daily_df)
    positive_count = len(daily_df[daily_df['논조'] == '긍정'])
    neutral_count = len(daily_df[daily_df['논조'] == '중립'])
    negative_count = len(daily_df[daily_df['논조'] == '부정'])
    
    m_col1, m_col2 = st.columns(2)
    with m_col1:
        st.metric("수집 기사", f"{total_count}건")
        st.metric("중립 보도", f"{neutral_count}건")
    with m_col2:
        st.metric("긍정 신호", f"{positive_count}건")
        st.metric("부정 신호", f"{negative_count}건")
else:
    st.info("해당 날짜에 수집된 기사 데이터가 없습니다.")

st.divider()

# 8. 카테고리 필터 (버튼 그룹)
if not daily_df.empty:
    categories = ['전체'] + list(daily_df['분야'].unique())
    
    # 카테고리 선택 상태 관리
    if 'selected_category' not in st.session_state:
        st.session_state.selected_category = '전체'
        
    # 가로 스크롤 가능한 컨테이너에 버튼 배치
    cat_cols = st.columns(len(categories))
    for i, cat in enumerate(categories):
        with cat_cols[i]:
            if st.button(cat, key=f"cat_{cat}"):
                st.session_state.selected_category = cat
                st.rerun()
                
    st.write("") # 여백
    
    # 선택된 카테고리 기사 필터링
    if st.session_state.selected_category == '전체':
        filtered_df = daily_df
    else:
        filtered_df = daily_df[daily_df['분야'] == st.session_state.selected_category]

    # 9. 기사 리스트 출력 (2-Pass 클러스터링으로 묶인 이슈별 아코디언)
    # 대표이슈(group_title)를 기준으로 그룹화
    grouped = filtered_df.groupby('대표이슈')
    
    for group_title, group_data in grouped:
        # 그룹의 첫 번째 기사를 대표로 사용
        rep_article = group_data.iloc[0]
        sentiment_color = "#4CAF50" if rep_article['논조'] == '긍정' else "#F44336" if rep_article['논조'] == '부정' else "#FFC107"
        
        with st.expander(f"{group_title} ({len(group_data)}건)"):
            st.markdown(f"<span style='color:{sentiment_color}; font-weight:bold;'>[{rep_article['논조']}]</span> <span style='color:#888;'>{rep_article['분야']}</span>", unsafe_allow_html=True)
            
            # AI 요약문 출력 박스
            st.markdown(f"<div class='summary-box'>{rep_article['AI요약']}</div>", unsafe_allow_html=True)
            
            # 묶인 원본 기사 링크 리스트
            for _, row in group_data.iterrows():
                st.markdown(f"- [{row['언론사']}] <a href='{row['기사링크']}' target='_blank' style='text-decoration:none; color:#4DA8DA;'>{row['제목']}</a>", unsafe_allow_html=True)
