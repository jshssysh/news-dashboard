import os
import html
import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="공정위 & 그룹동향 이슈 트래커",
    page_icon="📰",
    layout="wide"
)

st.title("📰 공정위 & 그룹동향 이슈 그룹화 대시보드")

file_path = "news_list.csv"

# 파일 존재 및 데이터 판별
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

# 상단 핵심 요약 지표 집계
total_count = len(df)
pos_count = len(df[df["논조"] == "긍정"])
neu_count = len(df[df["논조"] == "중립"])
neg_count = len(df[df["논조"] == "부정"])

# 상단 요약 지표 바 (1.8px 테두리 및 투명 배경)
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

st.divider()

# 분야별 기사 수 집계 및 라벨 매핑 사전 생성
cat_counts = df["분야"].value_counts().to_dict()

category_labels = [f"전체 ({total_count}건)"]
label_to_category = {f"전체 ({total_count}건)": "전체"}

unique_cats = [c for c in df["분야"].unique().tolist() if pd.notna(c)]
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
    filtered_df = df[df["분야"] == selected_category]
else:
    filtered_df = df

st.caption(f"선택된 분야: **{selected_category}** (총 **{len(filtered_df)}**건 보도)")
st.divider()

# 대표 이슈(group_title) 단위로 기사 그룹화
grouped = filtered_df.groupby("대표이슈", sort=False)

for issue_name, group_df in grouped:
    pos = len(group_df[group_df["논조"] == "긍정"])
    neu = len(group_df[group_df["논조"] == "중립"])
    neg = len(group_df[group_df["논조"] == "부정"])
    
    # 우세 논조 판단
    if neg >= pos and neg >= neu and neg > 0:
        dominant_label = "부정"
        badge_style = "border: 1.8px solid #dc3545; color: #dc3545;"
    elif pos >= neg and pos >= neu and pos > 0:
        dominant_label = "긍정"
        badge_style = "border: 1.8px solid #28a745; color: #28a745;"
    else:
        dominant_label = "중립"
        badge_style = "border: 1.8px solid #e0a800; color: #d39e00;"

    # 우세 논조 메인 기사 추출
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

    # HTML 특수문자 안전 변환 (검은 화면 방지 핵심 로직)
    safe_issue_name = html.escape(str(issue_name))
    safe_main_press = html.escape(str(raw_main_press))
    safe_main_summary = html.escape(str(raw_main_summary))
    safe_main_link = html.escape(str(raw_main_link))
    safe_category = html.escape(str(raw_category))

    # 논조 분포 테두리 태그 생성
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
        
        with st.expander(f"📂 언론사별 반응 및 관련 기사 보기 ({article_count}개 보도 기사 펼치기)"):
            for _, row in group_df.iterrows():
                sub_sentiment = row.get("논조", "중립")
                if sub_sentiment == "긍정":
                    sub_style = "border: 1.5px solid #28a745; color: #28a745;"
                elif sub_sentiment == "부정":
                    sub_style = "border: 1.5px solid #dc3545; color: #dc3545;"
                else:
                    sub_style = "border: 1.5px solid #e0a800; color: #d39e00;"
                    
                c1, c2 = st.columns([1.2, 4])
                with c1:
                    st.markdown(f"**[{row.get('언론사', '언론사미상')}]**")
                    st.markdown(f'<span style="{sub_style} border-radius: 5px; padding: 2px 8px; font-size: 12px; font-weight: 700; background-color: transparent;">{sub_sentiment}</span>', unsafe_allow_html=True)
                with c2:
                    st.markdown(f"[{row.get('제목', '제목없음')}]({row.get('기사링크', '#')})")
                    st.caption(f"요약: {row.get('AI요약', '')}")
                st.markdown("---")
        st.divider()
