import os
import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="공정위 & 그룹동향 이슈 트래커",
    page_icon="📰",
    layout="wide"
)

st.title("📰 공정위 & 그룹동향 이슈 그룹화 대시보드")

file_path = "news_list.csv"

# 1. 파일 존재 여부 확인
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

# 2. 구버전 CSV 예외 처리 (대표이슈 열 존재 확인)
if "대표이슈" not in df.columns:
    st.warning("⚠️ 이전 규격의 CSV 데이터가 남아있습니다. GitHub Actions의 'Run workflow'를 다시 실행하시면 최신 수집 데이터로 자동 교체됩니다.")
    st.stop()

# 상단 핵심 요약 지표
total_count = len(df)
pos_count = len(df[df["논조"] == "긍정"])
neu_count = len(df[df["논조"] == "중립"])
neg_count = len(df[df["논조"] == "부정"])

col1, col2, col3, col4 = st.columns(4)
col1.metric("총 수집 기사", f"{total_count}건")
col2.metric("🟢 긍정 기사", f"{pos_count}건")
col3.metric("⚪ 중립 기사", f"{neu_count}건")
col4.metric("🔴 부정 기사", f"{neg_count}건")

st.divider()

# 분야 선택 태그 버튼
categories = ["전체"] + [c for c in df["분야"].unique().tolist() if pd.notna(c)]

st.subheader("📌 분야 선택")
try:
    selected_category = st.pills(
        label="분야 선택",
        options=categories,
        default="전체",
        label_visibility="collapsed"
    )
except AttributeError:
    selected_category = st.radio(
        label="분야 선택",
        options=categories,
        horizontal=True,
        label_visibility="collapsed"
    )

if selected_category and selected_category != "전체":
    filtered_df = df[df["분야"] == selected_category]
else:
    filtered_df = df

st.caption(f"선택된 분야: **{selected_category}** (총 **{len(filtered_df)}**건 보도)")
st.divider()

# 대표 이슈(group_title) 단위로 기사 그룹화
grouped = filtered_df.groupby("대표이슈", sort=False)

for issue_name, group_df in grouped:
    main_press = group_df["언론사"].iloc[0] if "언론사" in group_df.columns else "언론사 미상"
    category = group_df["분야"].iloc[0]
    main_summary = group_df["AI요약"].iloc[0]
    article_count = len(group_df)
    
    # 그룹 내 긍부정 논조 분포 집계
    pos = len(group_df[group_df["논조"] == "긍정"])
    neu = len(group_df[group_df["논조"] == "중립"])
    neg = len(group_df[group_df["논조"] == "부정"])
    
    sentiment_badges = []
    if pos > 0: sentiment_badges.append(f"🟢 긍정 {pos}")
    if neu > 0: sentiment_badges.append(f"⚪ 중립 {neu}")
    if neg > 0: sentiment_badges.append(f"🔴 부정 {neg}")
    sentiment_str = " | ".join(sentiment_badges)

    # 이슈 대표 카드 표출
    with st.container():
        st.markdown(f"### 🔥 {issue_name}")
        st.markdown(f"**분야:** `{category}` | **메인 언론사:** `{main_press}` | **총 보도 매체:** `{article_count}개 언론사` | **논조 분포:** {sentiment_str}")
        st.info(f"💡 **AI 핵심 요약:** {main_summary}")
        
        # 언론사 세부 반응 펼치기 버튼
        with st.expander(f"📂 언론사별 반응 및 관련 기사 보기 ({article_count}개 보도 기사 펼치기)"):
            for _, row in group_df.iterrows():
                sub_icon = {
                    "긍정": "🟢 긍정",
                    "중립": "⚪ 중립",
                    "부정": "🔴 부정"
                }.get(row.get("논조", "중립"), "⚪ 중립")
                
                c1, c2 = st.columns([1, 4])
                with c1:
                    st.markdown(f"**[{row.get('언론사', '언론사미상')}]**")
                    st.caption(f"논조: `{sub_icon}`")
                with c2:
                    st.markdown(f"[{row.get('제목', '제목없음')}]({row.get('기사링크', '#')})")
                    st.caption(f"요약: {row.get('AI요약', '')}")
                st.markdown("---")
        st.divider()
