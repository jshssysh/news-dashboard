import os
import csv
import json
import time
import requests
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
    "삼성": "그룹동향",
    "계열분리": "그룹동향",
    "일감몰아주기": "부당지원",
    "웰스토리": "삼성/이슈",
    "삼우종합건축사사무소": "삼성/이슈",
    "레이크사이드cc": "삼성/이슈"
}

def get_naver_news_24h(keyword):
    url = f"https://openapi.naver.com/v1/search/news.json?query={keyword}&display=100&sort=date"
    headers = {
        "X-Naver-Client-Id": NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET
    }
    
    kst = timezone(timedelta(hours=9))
    now_kst = datetime.now(kst)
    time_threshold = now_kst - timedelta(hours=24)
    
    valid_items = []
    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            items = res.json().get("items", [])
            for item in items:
                pub_date_str = item.get("pubDate", "")
                try:
                    pub_dt = parsedate_to_datetime(pub_date_str)
                    if pub_dt >= time_threshold:
                        valid_items.append(item)
                    else:
                        break
                except Exception as e:
                    print(f"[WARN] 날짜 파싱 실패, 기본 포함: {e}")
                    valid_items.append(item)
        else:
            print(f"[ERROR] 네이버 API 호출 실패 (상태코드: {res.status_code})")
    except Exception as e:
        print(f"[EXCEPTION] 네이버 API 요청 예외: {e}")
        
    return valid_items

def clean_text(text):
    return text.replace("<b>", "").replace("</b>", "").replace("&quot;", '"').replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")

def analyze_with_gemini(title, description, link):
    if not GEMINI_API_KEY:
        return "언론사 미상", title, title, "중립"
        
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
    prompt = f"""다음 뉴스 기사의 URL, 제목, 요약 내용을 읽고 4가지 항목을 JSON으로 추출해줘:
1. press: 언론사명 (기사 URL domain이나 제목에서 추출. 예: 연합뉴스, 한국경제, 조선일보, 매일경제 등)
2. group_title: 유사한 기사들을 하나로 묶을 수 있는 대표 이슈 제목 (핵심 사건 중심으로 15자 이내 작성)
3. summary: 1문장 핵심 요약
4. sentiment: 긍정, 중립, 부정 중 하나

기사 URL: {link}
제목: {title}
내용: {description}

응답형식 JSON:
{{"press": "언론사명", "group_title": "대표이슈제목", "summary": "1문장요약", "sentiment": "긍정|중립|부정"}}
"""
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"response_mime_type": "application/json"}
    }
    try:
        res = requests.post(url, json=payload, timeout=10)
        if res.status_code == 200:
            data = res.json()
            text_response = data['candidates'][0]['content']['parts'][0]['text']
            result = json.loads(text_response)
            press = result.get("press", "언론사 미상")
            group_title = result.get("group_title", title)
            summary = result.get("summary", title)
            sentiment = result.get("sentiment", "중립")
            return press, group_title, summary, sentiment
    except Exception as e:
        print(f"Gemini API Error: {e}")
        
    return "언론사 미상", title, title, "중립"

def main():
    today_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    file_name = "news_list.csv"

    rows = []
    seen_links = set()

    for keyword, category in KEYWORDS.items():
        articles = get_naver_news_24h(keyword)
        for item in articles:
            link = item["originallink"] if item["originallink"] else item["link"]
            
            if link in seen_links:
                continue
            seen_links.add(link)

            title = clean_text(item["title"])
            desc = clean_text(item["description"])
            
            press, group_title, summary, sentiment = analyze_with_gemini(title, desc, link)
            rows.append([today_str, category, group_title, title, press, summary, sentiment, link])
            
            time.sleep(0.5)

    print(f"[INFO] 최근 24시간 중복 제거 후 최종 수집된 기사 수: {len(rows)}건")

    with open(file_name, mode="w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["수집일자", "분야", "대표이슈", "제목", "언론사", "AI요약", "논조", "기사링크"])
        if len(rows) > 0:
            writer.writerows(rows)

if __name__ == "__main__":
    main()
