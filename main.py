import os
import csv
import json
import requests
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

NAVER_CLIENT_ID = os.environ.get("NAVER_CLIENT_ID", "").strip().replace('"', '').replace("'", "")
NAVER_CLIENT_SECRET = os.environ.get("NAVER_CLIENT_SECRET", "").strip().replace('"', '').replace("'", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip().replace('"', '').replace("'", "")

KEYWORDS = {
    "공정거래위원회": "공정위/정책",
    "공정위 담합": "담합/불공정",
    "공정위 과징금": "제재/과징금",
    "공정위 기업결합": "M&A/규제"
}

def get_naver_news_24h(keyword):
    # 최대 100건을 요청하여 24시간 이내 기사 전수 검사
    url = f"https://openapi.naver.com/v1/search/news.json?query={keyword}&display=100&sort=date"
    headers = {
        "X-Naver-Client-Id": NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET
    }
    
    # 현재 시각(KST, UTC+9) 및 24시간 전 임계 시각 계산
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
                    # RFC 822 날짜 문자열 파싱
                    pub_dt = parsedate_to_datetime(pub_date_str)
                    
                    # 24시간 이내 기사만 수집
                    if pub_dt >= time_threshold:
                        valid_items.append(item)
                    else:
                        # 최신순 정렬이므로 24시간을 넘어간 기사가 나오면 탐색 종료
                        break
                except Exception as e:
                    print(f"[WARN] 날짜 파싱 실패, 기본 포함 처리: {e}")
                    valid_items.append(item)
        else:
            print(f"[ERROR] 네이버 API 호출 실패 (상태코드: {res.status_code})")
            print(f"[ERROR] 응답 내용: {res.text}")
    except Exception as e:
        print(f"[EXCEPTION] 네이버 API 요청 중 예외 발생: {e}")
        
    return valid_items

def clean_text(text):
    return text.replace("<b>", "").replace("</b>", "").replace("&quot;", '"').replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")

def analyze_with_gemini(title, description):
    if not GEMINI_API_KEY:
        return title, "중립"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
    prompt = f"다음 공정거래위원회 관련 뉴스 제목과 요약을 읽고, 1문장 핵심 요약과 논조(긍정/중립/부정 중 하나)를 판단해줘.\n제목: {title}\n내용: {description}\n응답형식 JSON: {{\"summary\": \"요약문\", \"sentiment\": \"긍정|중립|부정\"}}"
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
            return result.get("summary", title), result.get("sentiment", "중립")
    except Exception as e:
        print(f"Gemini API Error: {e}")
    return title, "중립"

def main():
    today_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    file_name = "news_list.csv"

    rows = []
    for keyword, category in KEYWORDS.items():
        articles = get_naver_news_24h(keyword)
        for item in articles:
            title = clean_text(item["title"])
            desc = clean_text(item["description"])
            link = item["originallink"] if item["originallink"] else item["link"]
            summary, sentiment = analyze_with_gemini(title, desc)
            rows.append([today_str, category, title, summary, sentiment, link])

    print(f"[INFO] 24시간 이내 수집된 기사 수: {len(rows)}건")

    with open(file_name, mode="w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["수집일자", "분야", "제목", "AI요약", "논조", "기사링크"])
        if len(rows) > 0:
            writer.writerows(rows)

if __name__ == "__main__":
    main()
