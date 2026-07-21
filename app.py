import os
import html
import pandas as pd
import streamlit as st
from datetime import timedelta

st.set_page_config(
    page_title="공정위 & 그룹동향 이슈 트래커",
    page_icon="📰",
    layout="wide"
)

file_path = "news_list.csv"

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

if "대표이슈" not in df.columns:
    st.warning("⚠️ 이전 규격의 CSV 데이터가 남아있습니다. GitHub Actions의 'Run workflow'를 다시 실행하시면 최신 수집 데이터로 자동 교체됩니다.")
    st.stop()

# 날짜 데이터 전처리 및 기본값 설정
df["날짜"] = pd.to_datetime(df["수집일자"]).dt.date
최신_날짜 = df["날짜"].max()

if "target_date" not in st.session_state:
    st.session_state.target_date = 최신_날짜

# CSS 주입: 버튼 및 날짜 위젯 폰트 크기/굵기 강조
st.markdown("""
<style>
/* 날짜 입력 위젯 텍스트 스타일 */
div[data-testid="stDateInput"] input {
    font-size: 18px !important;
    font-weight: 900 !important;
    text-align: center !important;
}
/* 버튼 텍스트 스타일 */
div[data-testid="stButton"] button p {
    font-size: 18px !important;
    font-weight: 900 !important;
}
</style>
""", unsafe_allow_html=True)

# 상단 헤더 레이아웃 분할
col_title, col_date = st.columns([6.5, 3.5])

with col_title:
    st.title("📰 공정위 & 그룹동향 이슈 그룹화 대시보드")

with col_date:
    st.markdown("<div style='margin-top: 15px;'></div>", unsafe_allow_html=True)
    btn_left, date_center, btn_right = st.columns([1.5, 4, 1.5])
    
    with btn_left:
        if st.button("< 이전", use_container_width=True):
            st.session_state.target_date -= timedelta(days=1)
            st.rerun()
            
    with date_center:
        selected = st.date_input(
            "날짜 선택", 
            value=st.session_state.target_date, 
            label_visibility="collapsed"
        )
        if selected != st.session_state.target_date:
            st.session_state.target_date = selected
            st.rerun()
            
    with btn_right:
        if st.button("다음 >", use_container_width=True):
            st.session_state.target_date += timedelta(days=1)
            st.rerun()

# 선택된 날짜 기준으로 데이터프레임 필터링
daily_df = df[df["날짜"] == st.session_state.target_date]

st.divider()

if daily_df.empty:
    st.info(f"ℹ️ {st.session_state.target_date.strftime('%Y년 %m월 %d일')}에 수집된 뉴스 데이터가 없습니다.")
    st.stop()

total_count = len(daily_df)
pos_count = len(daily_df[daily_df["논조"] == "긍정"])
neu_count = len(daily_df[daily_df["논조"] == "중립"])
neg_count = len(daily_df[daily_df["논조"] == "부정"])

st.markdown(f"""
<div style="display: flex; gap: 10px; align-items: center; justify-content: flex-start; margin-top: 5px; margin-bottom: 20px;">
    <div style="border: 1.8px solid #6c757d; border-radius: 8px; padding: 6px 14px; text-align: center; font-weight: 700; font-size: 14px; color: #343a40; background-color: transparent;">
        총({total_count})
    </div>
    <div style="border: 1.8px solid #28a745; border-radius: 8px; padding: 6px 14px; text-align: center; font-weight: 700; font-size: 14px; color: #28a745; background-color: transparent;">
        긍정({pos_count})
    </div>
    <div style="border: 1.8px solid #e0a800; border-radius: 8px; padding: 6px 14px; text-align: center; font-weight: 700; font-size: 14px; color: #d39e00; background-color: transparent;">
        중립({neu_count})
    </div>
    <div style="border: 1.8px solid #dc3545; border-radius: 8px; padding: 6px 14px; text-align: center; font-weight: 700; font-size: 14px; color: #dc3545; background-color: transparent;">
        부정({neg_count})
    </div>
</div>
""", unsafe_allow_html=True)

cat_counts = daily_df["분야"].value_counts().to_dict()

category_labels = [f"전체 ({total_count}건)"]
label_to_category = {f"전체 ({total_count}건)": "전체"}

unique_cats = [c for c in daily_df["분야"].unique().tolist() if pd.notna(c)]
for cat in unique_cats:
    count = cat_counts.get(cat, 0)
    label = f"{cat} ({count}건)"
    category_labels.append(label)
    label_to_category[label] = cat

st.subheader("📌 분야 선택")
try:
    selected_label = st.pills(
        label="분야 선택",
        options=category_labels,
        default=category_labels[0],
        label_visibility="collapsed"
    )
except AttributeError:
    selected_label = st.radio(
        label="분야 선택",
        options=category_labels,
        horizontal=True,
        label_visibility="collapsed"
    )

selected_category = label_to_category.get(selected_label, "전체")

if selected_category != "전체":
    filtered_df = daily_df[daily_df["분야"] == selected_category]
else:
    filtered_df = daily_df

st.caption(f"선택된 분야: **{selected_category}** (총 **{len(filtered_df)}**건 보도)")
st.divider()

grouped = filtered_df.groupby("대표이슈", sort=False)

for issue_name, group_df in grouped:
    pos = len(group_df[group_df["논조"] == "긍정"])
    neu = len(group_df[group_df["논조"] == "중립"])
    neg = len(group_df[group_df["논조"] == "부정"])
    
    if neg >= pos and neg >= neu and neg > 0:
        dominant_label = "부정"
        badge_style = "border: 1.8px solid #dc3545; color: #dc3545;"
    elif pos >= neg and pos >= neu and pos > 0:
        dominant_label = "긍정"
        badge_style = "border: 1.8px solid #28a745; color: #28a745;"
    else:
        dominant_label = "중립"
        badge_style = "border: 1.8px solid #e0a800; color: #d39e00;"

    dominant_articles = group_df[group_df["논조"] == dominant_label]
    if not dominant_articles.empty:
        main_article = dominant_articles.iloc[0]
    else:
        main_article = group_df.iloc[0]

    raw_main_press = main_article.get("언론사", "언론사 미상") if pd.notna(main_article.get("언론사")) else "언론사 미상"
    raw_main_summary = main_article.get("AI요약", "")
    raw_main_link = main_article.get("기사링크", "#")
    raw_category = group_df["분야"].iloc[0]
    article_count = len(group_df)

    safe_issue_name = html.escape(str(issue_name))
    safe_main_press = html.escape(str(raw_main_press))
    safe_main_summary = html.escape(str(raw_main_summary))
    safe_main_link = html.escape(str(raw_main_link))
    safe_category = html.escape(str(raw_category))

    sentiment_badges = []
    if pos > 0:
        sentiment_badges.append(f'<span style="border: 1.5px solid #28a745; border-radius: 6px; padding: 2px 8px; font-size: 12px; font-weight: 700; color: #28a745; background-color: transparent;">긍정 {pos}</span>')
    if neu > 0:
        sentiment_badges.append(f'<span style="border: 1.5px solid #e0a800; border-radius: 6px; padding: 2px 8px; font-size: 12px; font-weight: 700; color: #d39e00; background-color: transparent;">중립 {neu}</span>')
    if neg > 0:
        sentiment_badges.append(f'<span style="border: 1.5px solid #dc3545; border-radius: 6px; padding: 2px 8px; font-size: 12px; font-weight: 700; color: #dc3545; background-color: transparent;">부정 {neg}</span>')
    sentiment_html = " ".join(sentiment_badges)

    with st.container():
        st.markdown(f"""
        <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 8px;">
            <span style="{badge_style} border-radius: 6px; padding: 3px 10px; font-weight: 700; font-size: 14px; background-color: transparent; display: inline-block;">
                {dominant_label}
            </span>
            <span style="font-size: 20px; font-weight: 700; color: inherit;">
                <a href="{safe_main_link}" target="_blank" style="text-decoration: none; color: inherit;">
                    {safe_issue_name} <span style="font-size: 15px;">🔗</span>
                </a>
            </span>
        </div>
        """, unsafe_allow_html=True)
        
        st.markdown(f"**분야:** `{safe_category}` | **메인 언론사:** `{safe_main_press}` | **총 보도 매체:** `{article_count}개 언론사` | **논조 분포:** {sentiment_html}", unsafe_allow_html=True)
        st.info(f"💡 **AI 핵심 요약:** {safe_main_summary}")
        
        def render_article_list(df_items):
            for _, row in df_items.iterrows():
                sub_sentiment = row.get("논조", "중립")
                if sub_sentiment == "긍정":
                    sub_style = "border: 1.5px solid #28a745; color: #28a745;"
                elif sub_sentiment == "부정":
                    sub_style = "border: 1.5px solid #dc3545; color: #dc3545;"
                else:
                    sub_style = "border: 1.5px solid #e0a800; color: #d39e00;"
                    
                safe_sub_press = html.escape(str(row.get('언론사', '언론사미상')))
                safe_sub_title = html.escape(str(row.get('제목', '제목없음')))
                safe_sub_summary = html.escape(str(row.get('AI요약', '')))
                safe_sub_link = html.escape(str(row.get('기사링크', '#')))

                st.markdown(f"""
                <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 8px; font-size: 14px; line-height: 1.5; flex-wrap: wrap;">
                    <span style="{sub_style} border-radius: 5px; padding: 1px 6px; font-size: 12px; font-weight: 700; background-color: transparent; white-space: nowrap;">
                        {sub_sentiment}
                    </span>
                    <span style="font-weight: 700; white-space: nowrap;">
                        [{safe_sub_press}]
                    </span>
                    <a href="{safe_sub_link}" target="_blank" style="font-weight: 600; text-decoration: none; color: #1f77b4;">
                        {safe_sub_title}
                    </a>
                    <span style="color: #6c757d; font-size: 13px;">
                        - {safe_sub_summary}
                    </span>
                </div>
                """, unsafe_allow_html=True)
                st.markdown("<hr style='margin: 4px 0 8px 0; border: none; border-top: 1px solid #f0f2f6;' />", unsafe_allow_html=True)

        if article_count >= 2:
            with st.expander(f"📂 언론사별 반응 및 관련 기사 보기 ({article_count}개 보도 기사 펼치기)"):
                render_article_list(group_df)
        else:
            render_article_list(group_df)

        st.divider()
