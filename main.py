import os
import csv
import json
import time
import re
import requests
import pandas as pd
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

NAVER_CLIENT_ID = os.environ.get("NAVER_CLIENT_ID", "").strip().replace('"', '').replace("'", "")
NAVER_CLIENT_SECRET = os.environ.get("NAVER_CLIENT_SECRET", "").strip().replace('"', '').replace("'", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip().replace('"', '').replace("'", "")

KEYWORDS = {
    "공정거래 -부동산 -분양 -아파트 -지역화폐": "공정위/정책",
    "내부거래 -부동산 -아파트": "부당지원",
    "하도급 -부동산 -분양 -건설현장": "갑을관계",
    "상생협력 -농축산 -지자체": "동반성장",
    "상법 -강의 -시험": "지배구조",
    "지배구조": "지배구조",
    "종합상사 -채용": "산업동향",
    "삼성": "삼성그룹",
    "삼성 계열분리": "삼성그룹",
    "일감몰아주기": "부당지원",
    "웰스토리": "삼성그룹",
    "삼우종합건축사사무소": "삼성그룹",
    "레이크사이드cc": "삼성그룹",
    "삼성물산 -래미안 -분양": "삼성물산"
}

PRESS_DOMAINS = {
    "yna.co.kr": "연합뉴스", "chosun.com": "조선일보", "donga.com": "동아일보",
    "joongang.co.kr": "중앙일보", "hankyung.com": "한국경제", "mk.co.kr": "매일경제",
    "sedaily.com": "서울경제", "edaily.co.kr": "이데일리", "etnews.com": "전자신문",
    "mt.co.kr": "머니투데이", "moneytoday.co.kr": "머니투데이", "heraldcorp.com": "헤럴드경제",
    "fnnews.com": "파이낸셜뉴스", "khan.co.kr": "경향신문", "hani.co.kr": "한겨레",
    "seoul.co.kr": "서울신문", "sbs.co.kr": "SBS", "kbs.co.kr": "KBS",
    "mbc.co.kr": "MBC", "ytn.co.kr": "YTN", "jtbc.co.kr": "JTBC",
    "news1.kr": "뉴스1", "newsis.com": "뉴시스", "biz.chosun.com": "조선비즈",
    "ajunews.com": "아주경제", "asiatoday.co.kr": "아시아투데이"
}

NAVER_PRESS_CODES = {
    "001": "연합뉴스", "002": "프레시안", "003": "국민일보", "005": "국민일보",
    "008": "머니투데이", "009": "매일경제", "011": "서울경제", "014": "파이낸셜뉴스",
    "015": "한국경제", "016": "헤럴드경제", "018": "이데일리", "020": "동아일보",
    "021": "문화일보", "022": "세계일보", "023": "조선일보", "025": "중앙일보",
    "028": "한겨레", "032": "경향신문", "052": "YTN", "055": "SBS",
    "056": "KBS", "057": "MBN", "214": "MBC", "421": "뉴스1", "403": "뉴시스"
}

def extract_press_from_link(link):
    for domain, name in PRESS_DOMAINS.items():
        if domain in link:
            return name
    if "n.news.naver.com" in link or "news.naver.com" in link:
        parts = link.split("/")
        for i, part in enumerate(parts):
            if part == "article" and i + 1 < len(parts):
                code = parts[i+1]
                if code in NAVER_PRESS_CODES:
                    return NAVER_PRESS_CODES[code]
    return None

def get_naver_news_24h(keyword):
    valid_items = []
    headers = {
        "X-Naver-Client-Id": NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET
    }
    kst = timezone(timedelta(hours=9))
    now_kst = datetime.now(kst)
    time_threshold = now_kst - timedelta(hours=24)
    start = 1
    
    while start <= 1000:
        url = f"https://openapi.naver.com/v1/search/news.json?query={keyword}&display=100&start={start}&sort=date"
        try:
            res = requests.get(url, headers=headers, timeout=10)
            if res.status_code == 200:
                items = res.json().get("items", [])
                if not items: break
                stop_fetching = False
                for item in items:
                    pub_date_str = item.get("pubDate", "")
                    try:
                        pub_dt = parsedate_to_datetime(pub_date_str)
                        if pub_dt >= time_threshold:
                            valid_items.append(item)
                        else:
                            stop_fetching = True
                            break
                    except Exception as e:
                        valid_items.append(item)
                if stop_fetching: break
                start += 100
            else:
                break
        except Exception:
            break
    return valid_items

def clean_text(text):
    return text.replace("<b>", "").replace("</b>", "").replace("&quot;", '"').replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")

def normalize_title(text):
    return " ".join(re.sub(r'[^\w\s]', '', text).split())

def verify_and_adjust_category(category, title, description):
    text_content = (title + " " + description).replace(" ", "")
    if "삼성물산" in text_content: return "삼성물산"
    if category == "삼성그룹":
        if any(kw in text_content for kw in ["삼성", "웰스토리", "삼우종합건축", "레이크사이드"]): return "삼성그룹"
        else: return "공정위/정책"
    return category

# [Pass 1] 개별 기사 분석 
def analyze_batch_with_gemini(batch_items):
    if not GEMINI_API_KEY:
        return [(item["idx"], 10, item["known_press"] or "미상", normalize_title(item["title"]), item["title"], "중립") for item in batch_items]
        
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
    input_data = [{"idx": item["idx"], "title": item["title"], "description": item["description"], "known_press": item["known_press"] or "미상"} for item in batch_items]
        
    prompt = f"""당신은 기업 지배구조 및 공정거래위원회 정책 전문 애널리스트입니다.
입력 기사 목록: {json.dumps(input_data, ensure_ascii=False)}

분석 지침:
1. idx: 번호 유지
2. relevance_score: '대기업 동향, 공정위 규제, 지배구조, 상생협력' 관련 핵심 뉴스인지 1~10점 평가. (무관한 지역행사, 분양, 단순사건 등은 4점 이하)
3. group_title: (relevance_score 5점 이상일 때만 생성) 표준 대표 이슈명 (10자 이내 명사형)
4. press: 언론사명
5. summary: (relevance_score 5점 이상일 때만 생성) 1문장 핵심 요약
6. sentiment: (relevance_score 5점 이상일 때만 생성) 긍정, 중립, 부정 중 하나

주의: relevance_score가 4점 이하인 기사는 group_title, summary, sentiment 키를 생성하지 말고 제외할 것.
"""
    payload = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"response_mime_type": "application/json"}}
    
    try:
        res = requests.post(url, json=payload, timeout=30)
        if res.status_code == 200:
            parsed_list = json.loads(res.json()['candidates'][0]['content']['parts'][0]['text'])
            result_map = {r.get("idx"): (r.get("relevance_score", 10), r.get("press", "미상"), normalize_title(r.get("group_title", "")), r.get("summary", ""), r.get("sentiment", "중립")) for r in parsed_list}
            return [(item["idx"], *result_map.get(item["idx"], (10, item["known_press"] or "미상", normalize_title(item["title"]), item["title"], "중립"))) for item in batch_items]
    except Exception:
        pass
    return [(item["idx"], 10, item["known_press"] or "미상", normalize_title(item["title"]), item["title"], "중립") for item in batch_items]

# [Pass 2] 전체 이슈 통합 (Semantic Clustering)
def master_cluster_with_gemini(unique_issue_titles):
    if not GEMINI_API_KEY or not unique_issue_titles:
        return {title: title for title in unique_issue_titles}
        
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
    
    prompt = f"""당신은 뉴스 이슈 클러스터링 전문가입니다.
다음은 오늘 하루 수집된 개별 기사들의 1차 이슈명 목록입니다.
의미가 같거나 완전히 동일한 사건을 다루고 있는 맥락(예: '로봇 조직 신설'과 'RX사업추진실 신설')의 이슈들을 능동적으로 파악하여, 하나의 '통합 대표 이슈명(10자 이내 명사형)'으로 묶어주세요.

[초기 이슈명 목록]
{json.dumps(unique_issue_titles, ensure_ascii=False)}

지침:
1. 의미가 같은 이슈명들은 반드시 똑같은 'merged' 값으로 통일해야 합니다.
2. 다른 이슈들과 묶일 필요가 없는 독립적인 이슈는 original 값을 그대로 merged에 사용하세요.
3. 누락되는 original 이슈명이 단 하나도 없도록 모든 항목을 매핑해 주세요.

응답 JSON 배열 예시:
[
  {{"original": "원본이슈명1", "merged": "통합대표이슈명A"}},
  {{"original": "원본이슈명2", "merged": "통합대표이슈명A"}}
]
"""
    payload = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"response_mime_type": "application/json"}}
    
    try:
        res = requests.post(url, json=payload, timeout=30)
        if res.status_code == 200:
            parsed_list = json.loads(res.json()['candidates'][0]['content']['parts'][0]['text'])
            return {item.get("original", ""): item.get("merged", "") for item in parsed_list}
    except Exception as e:
        print(f"[WARN] 2차 마스터 클러스터링 예외: {e}")
        
    return {title: title for title in unique_issue_titles}

def save_and_merge_1year_data(new_rows, file_name="news_list.csv"):
    columns = ["수집일자", "분야", "대표이슈", "제목", "언론사", "AI요약", "논조", "기사링크"]
    new_df = pd.DataFrame(new_rows, columns=columns)
    if os.path.exists(file_name) and os.path.getsize(file_name) > 0:
        try:
            old_df = pd.read_csv(file_name)
            old_df["분야"] = old_df["분야"].replace({"그룹동향": "삼성그룹", "삼성/이슈": "삼성그룹"})
            combined_df = pd.concat([old_df, new_df], ignore_index=True)
        except Exception:
            combined_df = new_df
    else:
        combined_df = new_df

    combined_df = combined_df.drop_duplicates(subset=["기사링크"], keep="last")
    try:
        combined_df["dt"] = pd.to_datetime(combined_df["수집일자"], errors="coerce", utc=True)
        cutoff_date = pd.Timestamp.utcnow() - pd.Timedelta(days=365)
        combined_df = combined_df[combined_df["dt"] >= cutoff_date]
        combined_df = combined_df.drop(columns=["dt"])
    except Exception:
        pass
    combined_df.to_csv(file_name, index=False, encoding="utf-8-sig")
    print(f"[INFO] 누계 데이터 업데이트 완료: {len(combined_df)}건")

def main():
    today_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    all_articles = []
    seen_links, seen_titles, unique_for_api = set(), set(), {}
    idx = 0

    for keyword, category in KEYWORDS.items():
        articles = get_naver_news_24h(keyword)
        for item in articles:
            link = item["originallink"] if item["originallink"] else item["link"]
            if link in seen_links: continue
            seen_links.add(link)

            title = clean_text(item["title"])
            norm_t = normalize_title(title)
            desc = clean_text(item["description"])
            
            article_data = {
                "idx": idx, "category": verify_and_adjust_category(category, title, desc), 
                "title": title, "norm_t": norm_t, "description": desc, "link": link, 
                "known_press": extract_press_from_link(link), "today_str": today_str
            }
            all_articles.append(article_data)
            
            # 비용 절감용 (동일 제목 1건만 API 전송)
            if norm_t not in unique_for_api:
                api_data = article_data.copy()
                api_data["description"] = desc[:80]
                unique_for_api[norm_t] = api_data
            idx += 1

    api_items = list(unique_for_api.values())
    idx_to_norm_t = {item["idx"]: item["norm_t"] for item in api_items}
    batches = [api_items[i:i + 10] for i in range(0, len(api_items), 10)]
    
    analyzed_results = {} 
    
    # [실행] Pass 1: 개별 기사 분석
    for b_idx, batch in enumerate(batches):
        print(f"[진행도] {b_idx + 1} / {len(batches)} 1차 분석 중...")
        for r_idx, score, _, g_title, summary, sentiment in analyze_batch_with_gemini(batch):
            if norm_t := idx_to_norm_t.get(r_idx):
                analyzed_results[norm_t] = (score, g_title, summary, sentiment)
        time.sleep(1.0)

    # [실행] Pass 2: 마스터 이슈 클러스터링 (단 1회 API 호출)
    valid_group_titles = list(set([res[1] for res in analyzed_results.values() if res[0] >= 5 and res[1]]))
    if valid_group_titles:
        print(f"[INFO] 2차 능동형 마스터 통합 진행 중... (대상: {len(valid_group_titles)}종류)")
        master_mapping = master_cluster_with_gemini(valid_group_titles)
        
        # 1차 분석 결과에 2차 통합 이슈명 덮어쓰기
        for norm_t, (score, orig_gt, summary, sentiment) in analyzed_results.items():
            if score >= 5 and orig_gt in master_mapping:
                analyzed_results[norm_t] = (score, master_mapping[orig_gt], summary, sentiment)

    # [실행] Pass 3: 전체 기사 복원 및 CSV 저장
    rows = []
    for item in all_articles:
        norm_t = item["norm_t"]
        if norm_t in analyzed_results:
            score, group_title, summary, sentiment = analyzed_results[norm_t]
            if score < 5: continue
        else:
            group_title, summary, sentiment = normalize_title(item["title"]), item["title"], "중립"

        rows.append([
            item["today_str"], item["category"], group_title, item["title"],
            item["known_press"] or "미상", summary, sentiment, item["link"]
        ])

    print(f"[INFO] 최종 유효 기사 {len(rows)}건 병합 저장 완료")
    save_and_merge_1year_data(rows)

if __name__ == "__main__":
    main()
