import streamlit as st
import pandas as pd
import os

st.set_page_config(page_title="Daily Brief Dashboard", layout="wide")

st.title("📰 Daily Brief 뉴스 트래커")

if not os.path.exists("news_list.csv"):
    st.info("아직 수집된 뉴스 데이터가 없습니다. GitHub Actions를 먼저 수동 실행해주세요.")
    st.stop()

df = pd.read_csv("news_list.csv")

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

categories = ["전체"] + list(df['분야'].unique())
selected_category = st.selectbox("분야 선택", categories)
search_query = st.text_input("기사 제목/요약 검색", "")

filtered_df = df.copy()
if selected_category != "전체":
    filtered_df = filtered_df[filtered_df['분야'] == selected_category]

if search_query:
    filtered_df = filtered_df[
        filtered_df['제목'].str.contains(search_query, case=False, na=False) |
        filtered_df['AI요약'].str.contains(search_query, case=False, na=False)
    ]

st.subheader(f"뉴스 리스트 ({len(filtered_df)}건)")

for idx, row in filtered_df.iterrows():
    with st.container():
        st.markdown(f"**[{row['분야']}]** `{row['논조']}` | {row['수집일자']}")
        st.markdown(f"### [{row['제목']}]({row['기사링크']})")
        st.write(f"💡 **AI 요약:** {row['AI요약']}")
        st.divider()
