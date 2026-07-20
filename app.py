import streamlit as st
import pandas as pd
import os

st.set_page_config(page_title="Daily Brief Dashboard", layout="wide")
st.title("📰 Daily Brief 뉴스 트래커")

file_name = "news_list.csv"

if not os.path.exists(file_name):
    st.warning("⚠️ 아직 수집된 뉴스 데이터 파일(news_list.csv)이 없습니다.")
    st.info("GitHub 저장소의 Actions 탭에서 'Daily News Pipeline'을 수동 실행(Run workflow)해 주세요.")
    st.stop()

try:
    df = pd.read_csv(file_name)
except Exception as e:
    st.error(f"데이터 파일을 읽는 중 오류가 발생했습니다: {e}")
    st.stop()

if df.empty or "논조" not in df.columns:
    st.warning("⚠️ 현재 수집된 뉴스 데이터가 0건입니다.")
    st.info("네이버 API 키 설정(GitHub Secrets) 및 Actions 실행 로그를 확인해 주세요.")
    st.stop()

col1, col2, col3, col4 = st.columns(4)
total_count = len(df)
pos_count = len(df[df['논조'] == '긍정'])
neu_count = len(df[df['논조'] == '중립'])
neg_count = len(df[df['논조'] == '부정'])

col1.metric("총 수집 기사", f"{total_count}건")
col2.metric("긍정 논조", f"{pos_count}건")
col3.metric("중립 논조", f"{neu_count}건")
col4.metric("부정 논조", f"{neg_count}건")

st.divider()

categories = ["전체"] + list(df['분야'].dropna().unique())
selected_category = st.selectbox("분야 선택", categories)
search_query = st.text_input("기사 제목/요약 검색", "")

filtered_df = df.copy()
if selected_category != "전체":
    filtered_df = filtered_df[filtered_df['분야'] == selected_category]

if search_query:
    filtered_df = filtered_df[
        filtered_df['제목'].astype(str).str.contains(search_query, case=False, na=False) |
        filtered_df['AI요약'].astype(str).str.contains(search_query, case=False, na=False)
    ]

st.subheader(f"뉴스 리스트 ({len(filtered_df)}건)")

for idx, row in filtered_df.iterrows():
    with st.container():
        st.markdown(f"**[{row['분야']}]** `{row['논조']}` | {row['수집일자']}")
        st.markdown(f"### [{row['제목']}]({row['기사링크']})")
        st.write(f"💡 **AI 요약:** {row['AI요약']}")
        st.divider()
