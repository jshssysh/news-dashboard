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
    "공정거래": "공정위/정책",
    "내부거래": "부당지원",
    "하도급": "갑을관계",
    "상생협력": "동반성장",
    "상법": "지배구조",
    "지배구조": "지배구조",
    "종합상사": "산업동향",
    "삼성": "삼성그룹",
    "삼성 계열분리": "삼성그룹",
    "일감몰아주기": "부당지원",
    "웰스토리": "삼성그룹",
    "삼우종합건축사사무소": "삼성그룹",
    "레이크사이드cc": "삼성그룹",
    "삼성물산": "삼성물산"
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
    while start <= 1000: # 네이버 API 최대 start 값은 1000
        url = f"https://openapi.naver.com/v1/search/news.json?query={keyword}&display=100&start={start}&sort=date"
        try:
            res = requests.get(url, headers=headers, timeout=10)
            if res.status_code == 200:
                items = res.json().get("items", [])
                if not items:
                    break
                    
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
                        print(f"[WARN] 날짜 파싱 실패, 기본 포함: {e}")
                        valid_items.append(item)
                
                if stop_fetching:
                    break
                start += 100
            else:
                print(f"[ERROR] 네이버 API 호출 실패 (상태코드: {res.status_code})")
                break
        except Exception as e:
            print(f"[EXCEPTION] 네이버 API 요청 예외: {e}")
            break
            
    return valid_items

def clean_text(text):
    return text.replace("<b>", "").replace("</b>", "").replace("&quot;", '"').replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")

def normalize_title(text):
    text = re.sub(r'[^\w\s]', '', text)
    return " ".join(text.split())

def verify_and_adjust_category(category, title, description):
    text_content = (title + " " + description).replace(" ", "")
    
    if "삼성물산" in text_content:
        return "삼성물산"
        
    if category == "삼성그룹":
        samsung_keywords = ["삼성", "웰스토리", "삼우종합건축", "레이크사이드"]
        if any(kw in text_content for kw in samsung_keywords):
            return "삼성그룹"
        else:
            return "공정위/정책"
            
    return category

def analyze_batch_with_gemini(batch_items):
    if not GEMINI_API_KEY:
        return [(item["idx"], item["known_press"] or "언론사 미상", normalize_title(item["title"]), item["title"], "중립") for item in batch_items]
        
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
    
    input_data = []
    for item in batch_items:
        input_data.append({
            "idx": item["idx"],
            "title": item["title"],
            "description": item["description"],
            "known_press": item["known_press"] or "언론사 미상"
        })
        
    prompt = f"""다음 {len(input_data)}개의 기사 목록을 분석하여 각 기사별 결과를 JSON 배열(Array) 형태로 응답해줘.

입력 기사 목록:
{json.dumps(input_data, ensure_ascii=False)}

각 기사별 분석 지침:
1. idx: 입력받은 기사의 idx 번호 그대로 유지
2. group_title: 이 기사와 연관된 다른 뉴스들을 하나로 그룹화하기 위한 '표준 대표 이슈명' (10자 이내의 명사형 조합).
3. press: 언론사명 (알려진 언론사명 known_press를 최우선 사용)
4. summary: 1문장 핵심 요약
5. sentiment: 논조 판단 (긍정, 중립, 부정 중 하나)

응답형식 JSON 예시:
[
  {{"idx": 0, "press": "언론사명", "group_title": "표준대표이슈명", "summary": "1문장요약", "sentiment": "중립"}}
]
"""
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"response_mime_type": "application/json"}
    }
    
    try:
        res = requests.post(url, json=payload, timeout=15)
        if res.status_code == 200:
            data = res.json()
            text_response = data['candidates'][0]['content']['parts'][0]['text']
            parsed_list = json.loads(text_response)
            
            result_map = {}
            for r in parsed_list:
                r_idx = r.get("idx")
                press = r.get("press", "언론사 미상")
                gt = normalize_title(r.get("group_title", ""))
                summary = r.get("summary", "")
                sentiment = r.get("sentiment", "중립")
                if sentiment not in ["긍정", "중립", "부정"]:
                    sentiment = "중립"
                result_map[r_idx] = (press, gt, summary, sentiment)
                
            results = []
            for item in batch_items:
                i_idx = item["idx"]
                if i_idx in result_map:
                    p, g, s, sent = result_map[i_idx]
                    results.append((i_idx, p, g or normalize_title(item["title"]), s or item["title"], sent))
                else:
                    results.append((i_idx, item["known_press"] or "언론사 미상", normalize_title(item["title"]), item["title"], "중립"))
            return results
        else:
            print(f"[WARN] Gemini API 응답 상태 코드: {res.status_code}")
    except Exception as e:
        print(f"[WARN] Gemini API 요청 예외: {e}")
        
    return [(item["idx"], item["known_press"] or "언론사 미상", normalize_title(item["title"]), item["title"], "중립") for item in batch_items]

def save_and_merge_1year_data(new_rows, file_name="news_list.csv"):
    columns = ["수집일자", "분야", "대표이슈", "제목", "언론사", "AI요약", "논조", "기사링크"]
    new_df = pd.DataFrame(new_rows, columns=columns)
    
    if os.path.exists(file_name) and os.path.getsize(file_name) > 0:
        try:
            old_df = pd.read_csv(file_name)
            old_df["분야"] = old_df["분야"].replace({"그룹동향": "삼성그룹", "삼성/이슈": "삼성그룹"})
            combined_df = pd.concat([old_df, new_df], ignore_index=True)
        except Exception as e:
            print(f"[WARN] 기존 CSV 읽기 오류로 신규 생성: {e}")
            combined_df = new_df
    else:
        combined_df = new_df

    combined_df = combined_df.drop_duplicates(subset=["기사링크"], keep="last")
    
    try:
        # utc=True를 부여하여 aware 객체로 변환하고, 기준일자 역시 utcnow로 통일하여 타입 충돌 방지
        combined_df["dt"] = pd.to_datetime(combined_df["수집일자"], errors="coerce", utc=True)
        cutoff_date = pd.Timestamp.utcnow() - pd.Timedelta(days=365)
        combined_df = combined_df[combined_df["dt"] >= cutoff_date]
        combined_df = combined_df.drop(columns=["dt"])
    except Exception as e:
        print(f"[WARN] 날짜 필터링 중 예외 발생: {e}")

    combined_df.to_csv(file_name, index=False, encoding="utf-8-sig")
    print(f"[INFO] 1년 누계 데이터 업데이트 완료: 총 {len(combined_df)}건 보관 중")

def main():
    today_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    raw_articles = []
    seen_links = set()

    idx = 0
    for keyword, category in KEYWORDS.items():
        articles = get_naver_news_24h(keyword)
        for item in articles:
            link = item["originallink"] if item["originallink"] else item["link"]
            
            if link in seen_links:
                continue
            seen_links.add(link)

            title = clean_text(item["title"])
            desc = clean_text(item["description"])
            adjusted_category = verify_and_adjust_category(category, title, desc)
            known_press = extract_press_from_link(link)
            
            raw_articles.append({
                "idx": idx,
                "category": adjusted_category,
                "title": title,
                "description": desc,
                "link": link,
                "known_press": known_press,
                "today_str": today_str
            })
            idx += 1

    print(f"[INFO] 최종 분석 대상 기사 수: {len(raw_articles)}건")

    batch_size = 10
    batches = [raw_articles[i:i + batch_size] for i in range(0, len(raw_articles), batch_size)]
    
    print(f"[INFO] 10건 묶음 배치 생성 완료: 총 {len(batches)}개 API 요청 진행")

    analyzed_results = {}
    for b_idx, batch in enumerate(batches):
        results = analyze_batch_with_gemini(batch)
        for res in results:
            r_idx, press, group_title, summary, sentiment = res
            analyzed_results[r_idx] = (press, group_title, summary, sentiment)
            
        time.sleep(5.0)

    rows = []
    for item in raw_articles:
        i_idx = item["idx"]
        press, group_title, summary, sentiment = analyzed_results.get(
            i_idx, 
            (item["known_press"] or "언론사 미상", normalize_title(item["title"]), item["title"], "중립")
        )
        rows.append([
            item["today_str"],
            item["category"],
            group_title,
            item["title"],
            press,
            summary,
            sentiment,
            item["link"]
        ])

    print(f"[INFO] 금일 수집 및 배치 분석 완료: 총 {len(rows)}건")
    save_and_merge_1year_data(rows)

if __name__ == "__main__":
    main()
