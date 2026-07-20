import os
import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="공정위 & 그룹동향 뉴스 대시보드",
    page_icon="📰",
    layout="wide"
)

st.title("📰 공정위 & 그룹동향 실시간 뉴스 대시보드")

file_path = "news_list.csv"

# 파일 존재 및 데이터 확인
if not os.path.exists(file_path) or os.path.getsize(file_path) == 0:
    st.warning("⚠️ 현재 수집된 뉴스 데이터가 없습니다. GitHub Actions 실행 상태를 확인해 주세요.")
    st.stop()

try:
    df = pd.read_csv(file_path)
except Exception as e:
    st.error(f"❌ 데이터 읽기 오류: {e}")
    st.stop()

if df.empty:
    st.info("ℹ️ 현재 수집된 뉴스가 0건입니다.")
    st.stop()

# 상단 핵심 요약 지표 (Metrics)
total_count = len(df)
pos_count = len(df[df["논조"] == "긍정"])
neu_count = len(df[df["논조"] == "중립"])
neg_count = len(df[df["논조"] == "부정"])

col1, col2, col3, col4 = st.columns(4)
col1.metric("총 수집 기사", f"{total_count}건")
col2.metric("긍정 기사", f"{pos_count}건")
col3.metric("중립 기사", f"{neu_count}건")
col4.metric("부정 기사", f"{neg_count}건")

st.divider()

# 분야 목록 추출
categories = ["전체"] + [c for c in df["분야"].unique().tolist() if pd.notna(c)]

st.subheader("📌 분야 선택")

# 버튼 형태의 분야 선택기 (st.pills 적용)
try:
    selected_category = st.pills(
        label="분야 선택",
        options=categories,
        default="전체",
        label_visibility="collapsed"
    )
except AttributeError:
    # Streamlit 구버전 호환용 가로 버튼
    selected_category = st.radio(
        label="분야 선택",
        options=categories,
        horizontal=True,
        label_visibility="collapsed"
    )

# 필터링 로직
if selected_category and selected_category != "전체":
    filtered_df = df[df["분야"] == selected_category]
else:
    filtered_df = df

st.caption(f"선택된 분야: **{selected_category}** (총 **{len(filtered_df)}**건 조회됨)")
st.divider()

# 기사 카드 목록 출력
for idx, row in filtered_df.iterrows():
    sentiment_icon = {
        "긍정": "🟢 긍정",
        "중립": "⚪ 중립",
        "부정": "🔴 부정"
    }.get(row.get('논조', '중립'), "⚪ 중립")
    
    with st.container():
        st.markdown(f"### {row.get('제목', '제목 없음')}")
        st.markdown(f"**분야:** `{row.get('분야', '-')}` | **논조:** `{sentiment_icon}` | **수집일시:** `{row.get('수집일자', '-')}`")
        
        summary_text = row.get('AI요약', row.get('제목', ''))
        st.info(f"**AI 1문장 요약:** {summary_text}")
        
        link_url = row.get('기사링크', '#')
        st.markdown(f"[🔗 네이버 기사 원문 바로가기]({link_url})")
        st.divider()
