import os
import csv
import json
import requests
from datetime import datetime

NAVER_CLIENT_ID = os.environ.get("NAVER_CLIENT_ID", "").strip()
NAVER_CLIENT_SECRET = os.environ.get("NAVER_CLIENT_SECRET", "").strip()
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()

KEYWORDS = {
    "토목": "건설/토목",
    "부동산": "부동산",
    "인공지능": "IT/기술",
    "증시": "경제"
}

def get_naver_news(keyword):
    url = f"https://openapi.naver.com/v1/search/news.json?query={keyword}&display=5&sort=date"
    headers = {
        "X-Naver-Client-Id": NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
        "X-NCP-APIGW-API-KEY-ID": NAVER_CLIENT_ID,
        "X-NCP-APIGW-API-KEY": NAVER_CLIENT_SECRET
    }
    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            return res.json().get("items", [])
        else:
            print(f"[ERROR] 네이버 API 호출 실패 (상태코드: {res.status_code})")
            print(f"[ERROR] 응답 내용: {res.text}")
    except Exception as e:
        print(f"[EXCEPTION] 네이버 API 요청 중 예외 발생: {e}")
    return []

def clean_text(text):
    return text.replace("<b>", "").replace("</b>", "").replace("&quot;", '"').replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")

def analyze_with_gemini(title, description):
    if not GEMINI_API_KEY:
        return title, "중립"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
    prompt = f"다음 뉴스 제목과 요약을 읽고, 1문장 핵심 요약과 논조(긍정/중립/부정 중 하나)를 판단해줘.\n제목: {title}\n내용: {description}\n응답형식 JSON: {{\"summary\": \"요약문\", \"sentiment\": \"긍정|중립|부정\"}}"
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
    today_str = datetime.now().strftime("%Y-%m-%d")
    file_name = "news_list.csv"

    rows = []
    for keyword, category in KEYWORDS.items():
        articles = get_naver_news(keyword)
        for item in articles:
            title = clean_text(item["title"])
            desc = clean_text(item["description"])
            link = item["originallink"] if item["originallink"] else item["link"]
            summary, sentiment = analyze_with_gemini(title, desc)
            rows.append([today_str, category, title, summary, sentiment, link])

    print(f"[INFO] 총 수집된 기사 수: {len(rows)}건")

    if len(rows) > 0:
        # 기존 파일 삭제 후 새 데이터 작성
        with open(file_name, mode="w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["수집일자", "분야", "제목", "AI요약", "논조", "기사링크"])
            writer.writerows(rows)
    else:
        print("[WARN] 수집된 데이터가 없습니다. API 키를 확인해주세요.")

if __name__ == "__main__":
    main()
