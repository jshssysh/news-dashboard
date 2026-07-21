import os
import html
import pandas as pd
import streamlit as st
from datetime import timedelta

# 페이지 기본 설정 (와이드 레이아웃으로 변경하여 데스크탑 공간 확보)
st.set_page_config(
    page_title="Daily Brief",
    page_icon="📰",
    layout="wide" 
)

# ---------------------------------------------------------
# 1. 반응형 CSS 주입 (데스크탑/모바일 최적화 및 다크모드)
# ---------------------------------------------------------
st.markdown("""
<style>
    /* 전체 배경 다크모드 및 중앙 최대 너비 제한 (PC 환경에서 너무 퍼지는 것 방지) */
    .stApp { background-color: #121212; color: #e5e5e5; }
    .block-container { max-width: 1100px !important; padding-top: 2rem !important; }
    
    /* 날짜 선택 위젯 */
    div[data-testid="stDateInput"] input {
        font-size: 16px !important; font-weight: 700 !important; text-align: center !important;
        background-color: #2c2c2e !important; color: white !important;
        border-radius: 8px !important; border: none !important;
    }
    
    /* -----------------------------------
       반응형 그리드 및 카드 디자인
       ----------------------------------- */
    .grid-container { display: grid; gap: 16px; margin-bottom: 24px; }
    .grid-item { background-color: #1c1c1e; border-radius: 12px; border: 1px solid #2c2c2e; }
    .grid-title { color: #8e8e93; font-size: 13px; margin-bottom: 8px; }
    
    .brief-card { background-color: #1c1c1e; border-radius: 12px; margin-top: 16px; margin-bottom: 4px; border: 1px solid #2c2c2e; }
    .brief-card.neg { border-left: 4px solid #ef4444; }
    .brief-card.pos { border-left: 4px solid #3b82f6; }
    .brief-card.neu { border-left: 4px solid #f59e0b; }
    
    .card-tags { display: flex; gap: 8px; font-size: 12px; margin-bottom: 12px; align-items: center; }
    .tag-neg { color: #ef4444; background: rgba(239,68,68,0.15); padding: 3px 8px; border-radius: 6px; font-weight: bold; }
    .tag-pos { color: #3b82f6; background: rgba(59,130,246,0.15); padding: 3px 8px; border-radius: 6px; font-weight: bold; }
    .tag-neu { color: #f59e0b; background: rgba(245,158,11,0.15); padding: 3px 8px; border-radius: 6px; font-weight: bold; }
    .tag-cat { color: #60a5fa; background: rgba(96,165,250,0.15); padding: 3px 8px; border-radius: 6px; }
    .tag-date { color: #8e8e93; font-size: 11px; margin-left: auto; }
    
    .card-source { color: #8e8e93; font-size: 12px; margin-bottom: 8px; }
    .card-title { font-weight: bold; color: white; margin-bottom: 12px; line-height: 1.4; }
    .card-title-badge { background-color: #3b82f6; color: white; font-size: 12px; padding: 2px 8px; border-radius: 12px; margin-right: 6px; vertical-align: middle; }
    .card-summary { color: #aeaeb2; line-height: 1.5; margin-bottom: 0px; }
    
    .streamlit-expanderHeader { background-color: #1c1c1e !important; color: #60a5fa !important; border: none !important; border-radius: 8px !important; font-size: 13px !important; }

    /* 모바일 디바이스 최적화 (가로 폭 767px 이하) */
    @media (max-width: 767px) {
        .grid-container { grid-template-columns: repeat(2, 1fr); }
        .grid-item { padding: 16px; }
        .grid-value { font-size: 22px; font-weight: bold; color: white; }
        .brief-card { padding: 16px; }
        .card-title { font-size: 16px; }
        .card-summary { font-size: 13px; }
    }

    /* 데스크탑 모니터 최적화 (가로 폭 768px 이상) */
    @media (min-width: 768px) {
        .grid-container { grid-template-columns: repeat(4, 1fr); }
        .grid-item { padding: 20px; }
        .grid-value { font-size: 28px; font-weight: bold; color: white; }
        .brief-card { padding: 24px; }
        .card-title { font-size: 19px; }
        .card-summary { font-size: 15px; }
    }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------
# 2. 데이터 로드 및 초기 검증
# ---------------------------------------------------------
file_path = "news_list.csv"

if not os.path.exists(file_path) or os.path.getsize(file_path) == 0:
    st.warning("⚠️ 현재 수집된 뉴스 데이터가 없습니다.")
    st.stop()

try:
    df = pd.read_csv(file_path)
except Exception as e:
    st.error(f"❌ 데이터 읽기 오류: {e}")
    st.stop()

if df.empty or "대표이슈" not in df.columns:
    st.info("ℹ️ 현재 표시할 데이터가 없습니다.")
    st.stop()

df["날짜"] = pd.to_datetime(df["수집일자"]).dt.date
최신_날짜 = df["날짜"].max()

if "target_date" not in st.session_state:
    st.session_state.target_date = 최신_날짜

# ---------------------------------------------------------
# 3. 커스텀 헤더 및 요일 계산
# ---------------------------------------------------------
days_kr = ["월", "화", "수", "목", "금", "토", "일"]
target_day_kr = days_kr[st.session_state.target_date.weekday()]
formatted_date = f"{st.session_state.target_date.strftime('%Y년 %m월 %d일')} ({target_day_kr})"

st.markdown(f"""
<div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 20px; padding-top: 10px; flex-wrap: wrap; gap: 10px;">
    <div style="display: flex; align-items: center; gap: 12px;">
        <div style="background-color: #3b82f6; color: white; width: 36px; height: 36px; border-radius: 12px; display: flex; justify-content: center; align-items: center; font-weight: bold; font-size: 18px;">D</div>
        <div style="font-size: 24px; font-weight: bold; color: white;">Daily Brief</div>
    </div>
    <div style="color: #8e8e93; font-size: 13px; text-align: right; min-width: 150px;">
        {formatted_date} 발행
    </div>
</div>
""", unsafe_allow_html=True)

# ---------------------------------------------------------
# 4. 날짜 이동 컨트롤러
# ---------------------------------------------------------
col_space, col_nav = st.columns([6, 4]) 

with col_nav:
    btn_left, date_center, btn_right = st.columns([1.5, 4, 1.5])
    with btn_left:
        if st.button("◀", use_container_width=True):
            st.session_state.target_date -= timedelta(days=1)
            st.rerun()
    with date_center:
        selected = st.date_input("날짜", value=st.session_state.target_date, label_visibility="collapsed")
        if selected != st.session_state.target_date:
            st.session_state.target_date = selected
            st.rerun()
    with btn_right:
        if st.button("▶", use_container_width=True):
            st.session_state.target_date += timedelta(days=1)
            st.rerun()

st.markdown("<div style='margin-bottom: 10px;'></div>", unsafe_allow_html=True)

# ---------------------------------------------------------
# 5. 데이터 필터링 및 요약 지표 출력 (반응형 적용됨)
# ---------------------------------------------------------
daily_df = df[df["날짜"] == st.session_state.target_date]

if daily_df.empty:
    st.info(f"ℹ️ {formatted_date}에 수집된 뉴스 데이터가 없습니다.")
    st.stop()

total_count = len(daily_df)
pos_count = len(daily_df[daily_df["논조"] == "긍정"])
neu_count = len(daily_df[daily_df["논조"] == "중립"])
neg_count = len(daily_df[daily_df["논조"] == "부정"])

st.markdown(f"""
<div class="grid-container">
    <div class="grid-item">
        <div class="grid-title">수집 기사</div>
        <div class="grid-value">{total_count}<span style="font-size: 14px; font-weight: normal; color: #8e8e93;">건</span></div>
    </div>
    <div class="grid-item">
        <div class="grid-title">긍정 신호</div>
        <div class="grid-value">{pos_count}<span style="font-size: 14px; font-weight: normal; color: #8e8e93;">건</span></div>
    </div>
    <div class="grid-item">
        <div class="grid-title">중립 보도</div>
        <div class="grid-value">{neu_count}<span style="font-size: 14px; font-weight: normal; color: #8e8e93;">건</span></div>
    </div>
    <div class="grid-item">
        <div class="grid-title">부정 신호</div>
        <div class="grid-value" style="color: #ef4444;">{neg_count}<span style="font-size: 14px; font-weight: normal; color: #8e8e93;">건</span></div>
    </div>
</div>
""", unsafe_allow_html=True)

# ---------------------------------------------------------
# 6. 분야 선택 가로 스크롤 (Pills)
# ---------------------------------------------------------
cat_counts = daily_df["분야"].value_counts().to_dict()
category_labels = [f"전체 {total_count}"]
label_to_category = {f"전체 {total_count}": "전체"}

unique_cats = [c for c in daily_df["분야"].unique().tolist() if pd.notna(c)]
for cat in unique_cats:
    count = cat_counts.get(cat, 0)
    label = f"{cat} {count}"
    category_labels.append(label)
    label_to_category[label] = cat

selected_label = st.pills(
    label="분야 선택",
    options=category_labels,
    default=category_labels[0],
    label_visibility="collapsed"
)

selected_category = label_to_category.get(selected_label, "전체")

if selected_category != "전체":
    filtered_df = daily_df[daily_df["분야"] == selected_category]
else:
    filtered_df = daily_df

st.divider()

# ---------------------------------------------------------
# 7. 기사 렌더링 (반응형 적용됨)
# ---------------------------------------------------------
grouped = filtered_df.groupby("대표이슈", sort=False)

for issue_name, group_df in grouped:
    pos = len(group_df[group_df["논조"] == "긍정"])
    neu = len(group_df[group_df["논조"] == "중립"])
    neg = len(group_df[group_df["논조"] == "부정"])
    
    if neg >= pos and neg >= neu and neg > 0:
        dominant_label, card_class, tag_class = "부정", "neg", "tag-neg"
    elif pos >= neg and pos >= neu and pos > 0:
        dominant_label, card_class, tag_class = "긍정", "pos", "tag-pos"
    else:
        dominant_label, card_class, tag_class = "중립", "neu", "tag-neu"

    dominant_articles = group_df[group_df["논조"] == dominant_label]
    main_article = dominant_articles.iloc[0] if not dominant_articles.empty else group_df.iloc[0]

    raw_main_press = main_article.get("언론사", "언론사 미상") if pd.notna(main_article.get("언론사")) else "언론사 미상"
    raw_main_summary = main_article.get("AI요약", "")
    raw_main_link = main_article.get("기사링크", "#")
    raw_category = group_df["분야"].iloc[0]
    article_count = len(group_df)
    date_display = st.session_state.target_date.strftime("%m.%d.")

    safe_issue_name = html.escape(str(issue_name))
    safe_main_press = html.escape(str(raw_main_press))
    safe_main_summary = html.escape(str(raw_main_summary))
    safe_main_link = html.escape(str(raw_main_link))
    safe_category = html.escape(str(raw_category))

    st.markdown(f"""
    <div class="brief-card {card_class}">
        <div class="card-tags">
            <span class="{tag_class}">{dominant_label}</span>
            <span class="tag-cat">{safe_category}</span>
            <span class="tag-date">{date_display}</span>
        </div>
        <div class="card-source">{safe_main_press}</div>
        <div class="card-title">
            <span class="card-title-badge">이슈</span>
            <a href="{safe_main_link}" target="_blank" style="color: white; text-decoration: none;">{safe_issue_name}</a>
        </div>
        <div class="card-summary">{safe_main_summary}</div>
    </div>
    """, unsafe_allow_html=True)
    
    if article_count > 1:
        with st.expander(f"관련 보도 {article_count}건 전체보기"):
            for _, row in group_df.iterrows():
                sub_sentiment = row.get("논조", "중립")
                safe_sub_press = html.escape(str(row.get('언론사', '언론사미상')))
                safe_sub_title = html.escape(str(row.get('제목', '제목없음')))
                safe_sub_link = html.escape(str(row.get('기사링크', '#')))

                color_code = "#f59e0b"
                if sub_sentiment == "긍정": color_code = "#3b82f6"
                elif sub_sentiment == "부정": color_code = "#ef4444"

                st.markdown(f"""
                <div style="font-size: 13px; margin-bottom: 8px; line-height: 1.4;">
                    <span style="color: {color_code}; font-weight: bold;">[{sub_sentiment}]</span> 
                    <span style="color: #8e8e93;">{safe_sub_press}</span> 
                    <a href="{safe_sub_link}" target="_blank" style="color: #e5e5e5; text-decoration: none;">{safe_sub_title}</a>
                </div>
                """, unsafe_allow_html=True)
