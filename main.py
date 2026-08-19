import os
import json
import time
import re
import difflib
import requests
import yaml
import pandas as pd
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

NAVER_CLIENT_ID = os.environ.get("NAVER_CLIENT_ID", "").strip().replace('"', '').replace("'", "")
NAVER_CLIENT_SECRET = os.environ.get("NAVER_CLIENT_SECRET", "").strip().replace('"', '').replace("'", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip().replace('"', '').replace("'", "")

# 개발/UI 테스트용: true면 Gemini 분석을 건너뛰고 수집만 해서 저장 (빠르고 무료)
SKIP_AI_ANALYSIS = os.environ.get("SKIP_AI_ANALYSIS", "").strip().lower() == "true"

# GitHub Actions 러너는 UTC로 동작하므로, 날짜/시각은 항상 한국시간(KST) 기준으로 명시해서 사용한다
KST = timezone(timedelta(hours=9))

KEYWORDS_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config", "keywords.yaml")

def load_keywords(path=KEYWORDS_CONFIG_PATH):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)["keywords"]

KEYWORDS = load_keywords()

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

# 키워드 검색으로 붙는 분야는 임시 힌트일 뿐이며, Gemini가 기사 본문 기준으로 최종 확정한다
CATEGORY_LIST = [
    "삼성그룹", "삼성물산", "공정위/정책", "부당지원", "갑을관계",
    "동반성장", "지배구조", "산업동향", "제재·심결", "그린·AI워싱",
]

def extract_press_from_link(link):
    for domain, name in PRESS_DOMAINS.items():
        if domain in link: return name
    if "n.news.naver.com" in link or "news.naver.com" in link:
        parts = link.split("/")
        for i, part in enumerate(parts):
            if part == "article" and i + 1 < len(parts):
                if parts[i+1] in NAVER_PRESS_CODES: return NAVER_PRESS_CODES[parts[i+1]]
    return None

def get_naver_news_24h(keyword):
    valid_items = []
    headers = {"X-Naver-Client-Id": NAVER_CLIENT_ID, "X-Naver-Client-Secret": NAVER_CLIENT_SECRET}
    time_threshold = datetime.now(KST) - timedelta(hours=24)
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
                    try:
                        if parsedate_to_datetime(item.get("pubDate", "")) >= time_threshold:
                            valid_items.append(item)
                        else:
                            stop_fetching = True
                            break
                    except Exception: valid_items.append(item)
                if stop_fetching: break
                start += 100
            else: break
        except Exception: break
    return valid_items

def clean_text(text):
    return text.replace("<b>", "").replace("</b>", "").replace("&quot;", '"').replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")

def normalize_title(text):
    text = re.sub(r'\[.*?\]|\(.*?\)|\<.*?\>', '', text)
    return " ".join(re.sub(r'[^\w\s]', '', text).split())

# 완전히 같은 제목이 아니어도(같은 사건, 다른 표현) 겹치는 기사는 대표 1건만 Gemini에 보내 비용을 아낀다
TITLE_SIMILARITY_THRESHOLD = 0.65

def cluster_similar_titles(norm_titles_by_category, threshold=TITLE_SIMILARITY_THRESHOLD):
    """분야별로, 제목이 비슷한 기사들을 그룹으로 묶는다. (다른 분야끼리는 비교하지 않음)
    반환값: {원본 norm_t: 그룹 대표 norm_t}"""
    mapping = {}
    for norm_titles in norm_titles_by_category.values():
        leaders = []
        for nt in norm_titles:
            # SequenceMatcher는 seq2 정보를 캐싱하므로 nt를 seq2에 고정하고 leader만 바꿔가며 비교.
            # ratio()는 비싸므로 real_quick_ratio → quick_ratio 로 싸게 걸러낸 뒤에만 호출한다.
            sm = difflib.SequenceMatcher()
            sm.set_seq2(nt)
            leader = None
            for l in leaders:
                sm.set_seq1(l)
                if sm.real_quick_ratio() >= threshold and sm.quick_ratio() >= threshold and sm.ratio() >= threshold:
                    leader = l
                    break
            if leader is None:
                leaders.append(nt)
                leader = nt
            mapping[nt] = leader
    return mapping

def verify_and_adjust_category(category, title, description):
    text_content = (title + " " + description).replace(" ", "")
    if "삼성물산" in text_content: return "삼성물산"
    if category == "삼성그룹":
        if any(kw in text_content for kw in ["삼성", "웰스토리", "삼우종합건축", "레이크사이드"]): return "삼성그룹"
        else: return "공정위/정책"
    return category

def force_merge_by_keywords(title, original_group_title):
    t_lower = title.replace(" ", "")
    if "RX사업추진실" in t_lower or ("대표이사직속" in t_lower and "로봇" in t_lower):
        return "삼성전자 RX사업추진실 신설"
    return original_group_title

def analyze_batch_with_gemini(batch_items):
    """기사 배치를 Gemini에 보내 (idx, 관련도, 대표이슈명, 요약, 논조, 분야)를 한 번에 판정받는다."""
    if not GEMINI_API_KEY:
        return [(item["idx"], 0, "API 키 오류", "분석 에러", "판단 실패", None) for item in batch_items]

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent?key={GEMINI_API_KEY}"
    input_data = [{"idx": item["idx"], "title": item["title"], "description": item["description"], "original_category": item["category"]} for item in batch_items]

    # '기자의 서술 태도'가 아닌 '기업 호재/악재' 기준으로 논조를 강제 평가하도록 지시
    prompt = f"""당신은 기업 지배구조 및 공정거래위원회 정책 전문 애널리스트입니다.
입력 기사 목록: {json.dumps(input_data, ensure_ascii=False)}

분석 지침:
1. idx: 번호 유지
2. relevance_score: '대기업 동향, 공정위 규제, 지배구조, 상생협력' 관련 핵심 뉴스인지 1~10점 평가.
3. group_title: (relevance_score 5점 이상일 때만) 표준 대표 이슈명 (10자 이내 명사형)
4. summary: (relevance_score 5점 이상일 때만) 1문장 핵심 요약
5. sentiment: (relevance_score 5점 이상일 때만) '기자의 서술 태도'가 아닌 '해당 사건이 기업에 미치는 사업적/재무적 영향(호재/악재)'을 기준으로 판별할 것.
   - 긍정: 신사업, M&A, 조직 신설, 실적 개선, 투자 등 호재
   - 부정: 공정위 제재, 과징금, 법적 분쟁, 갑질 논란, 하도급 위반 등 악재
   - 중립: 단순 시황, 영향 미미한 인사 동정
   반드시 긍정, 중립, 부정 중 하나로만 출력.
6. category: relevance_score와 무관하게 모든 기사에 대해 판정. 아래 후보 중 기사 내용에 가장 적합한 것 정확히 하나만 선택.
   후보: {", ".join(CATEGORY_LIST)}
   original_category는 검색 키워드로 임시 배정된 힌트일 뿐이므로, 실제 기사 내용과 맞지 않으면 반드시 올바른 값으로 교정할 것.

주의: relevance_score가 4점 이하인 기사는 group_title, summary, sentiment 생성 제외 (category는 항상 생성).
"""
    payload = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"response_mime_type": "application/json"}}

    try:
        res = requests.post(url, json=payload, timeout=30)
        if res.status_code == 200:
            raw_text = res.json()['candidates'][0]['content']['parts'][0]['text'].strip()
            if raw_text.startswith("```json"): raw_text = raw_text[7:]
            if raw_text.startswith("```"): raw_text = raw_text[3:]
            if raw_text.endswith("```"): raw_text = raw_text[:-3]

            parsed_list = json.loads(raw_text.strip())
            result_map = {}
            for r in parsed_list:
                r_idx = r.get("idx")
                score = r.get("relevance_score") or 0
                # 관련도가 낮은 기사는 group_title/summary를 아예 생략하는 대신 JSON null로 주는 경우가 있어,
                # get()의 기본값이 안 먹힌다 (키는 있고 값이 None). "or" 로 한 번 더 방어해서
                # 기사 1건의 null 값 때문에 배치 20건 전체가 예외로 날아가는 걸 방지한다.
                g_title = normalize_title(r.get("group_title") or "")
                summary = r.get("summary") or ""

                sentiment = r.get("sentiment")
                if sentiment not in ["긍정", "중립", "부정"]:
                    # score>=5인데 sentiment가 없으면 진짜 이상치(기술적 문제) → "판단 실패"로 표시해 노출
                    # score<5는 프롬프트 지침상 정상적으로 sentiment를 생성하지 않은 것 → None으로 구분해 필터링 대상으로 남김
                    sentiment = "판단 실패" if score >= 5 else None

                category = r.get("category")
                if category not in CATEGORY_LIST:
                    category = None

                result_map[r_idx] = (score, g_title, summary, sentiment, category)

            return [(item["idx"], *result_map.get(item["idx"], (0, "파싱 오류", "데이터 구조 불일치", "판단 실패", None))) for item in batch_items]
        else:
            print(f"[Gemini API 오류] status={res.status_code} body={res.text[:300]}")
    except Exception as e:
        print(f"[Gemini API 예외] {e}")
    return [(item["idx"], 0, "통신 예외 발생", "분석 에러", "판단 실패", None) for item in batch_items]

def master_cluster_with_gemini(unique_issue_titles):
    if not GEMINI_API_KEY or not unique_issue_titles: return {title: title for title in unique_issue_titles}
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent?key={GEMINI_API_KEY}"
    prompt = f"""당신은 뉴스 이슈 클러스터링 전문가입니다.
[초기 이슈명 목록]
{json.dumps(unique_issue_titles, ensure_ascii=False)}
의미가 같은 사건을 다루는 이슈들을 능동적으로 파악하여 하나의 '통합 대표 이슈명(10자 이내 명사형)'으로 묶어주세요.
응답 JSON 배열 예시:
[
  {{"original": "원본이슈명1", "merged": "통합대표이슈명A"}}
]
"""
    payload = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"response_mime_type": "application/json"}}
    try:
        res = requests.post(url, json=payload, timeout=30)
        if res.status_code == 200:
            raw_text = res.json()['candidates'][0]['content']['parts'][0]['text'].strip()
            if raw_text.startswith("```json"): raw_text = raw_text[7:]
            if raw_text.startswith("```"): raw_text = raw_text[3:]
            if raw_text.endswith("```"): raw_text = raw_text[:-3]
            parsed_list = json.loads(raw_text.strip())
            return {item.get("original", ""): item.get("merged", "") for item in parsed_list}
        else:
            print(f"[Gemini 클러스터링 오류] status={res.status_code} body={res.text[:300]}")
    except Exception as e:
        print(f"[Gemini 클러스터링 예외] {e}")
    return {title: title for title in unique_issue_titles}

def save_and_merge_data(new_rows, file_name="news_list.csv"):
    columns = ["수집일자", "분야", "대표이슈", "제목", "언론사", "AI요약", "논조", "중요도", "기사링크", "발행일시"]
    new_df = pd.DataFrame(new_rows, columns=columns)
    if os.path.exists(file_name) and os.path.getsize(file_name) > 0:
        try:
            old_df = pd.read_csv(file_name)
            old_df["분야"] = old_df["분야"].replace({"그룹동향": "삼성그룹", "삼성/이슈": "삼성그룹"})
            if "중요도" not in old_df.columns:
                old_df["중요도"] = 5  # 구버전 데이터: 중요도 정보 없음 → 중간값으로 채움
            if "발행일시" not in old_df.columns:
                old_df["발행일시"] = ""  # 구버전 데이터: 발행 시각 미보존
            combined_df = pd.concat([old_df, new_df], ignore_index=True)
        except Exception: combined_df = new_df
    else: combined_df = new_df

    combined_df["중요도"] = pd.to_numeric(combined_df["중요도"], errors="coerce").fillna(5).astype(int)
    # 같은 기사링크가 여러 번 저장된 경우, 실제 AI 분석된 행이 "미분석"(개발 모드 플레이스홀더) 행에 덮어써지지 않도록
    # 미분석 행을 먼저 정렬해서 최우선으로 밀어내고, 분석된 행 중에서는 최신 것이 남도록 한다
    combined_df["_priority"] = (combined_df["논조"] != "미분석").astype(int)
    combined_df = combined_df.sort_values("_priority", kind="stable").drop(columns=["_priority"])
    combined_df = combined_df.drop_duplicates(subset=["기사링크"], keep="last")
    try:
        combined_df["dt"] = pd.to_datetime(combined_df["수집일자"], errors="coerce", utc=True)
        cutoff_date = pd.Timestamp.utcnow() - pd.Timedelta(days=30)
        combined_df = combined_df[combined_df["dt"] >= cutoff_date]
        combined_df = combined_df.drop(columns=["dt"])
    except Exception: pass
    
    combined_df.to_csv(file_name, index=False, encoding="utf-8-sig")

def main():
    today_str = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    all_articles = []
    seen_links, unique_for_api = set(), {}
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

            # 네이버가 주는 실제 기사 발행 시각(RFC 2822)을 KST 문자열로 변환해 보존
            try:
                pub_date = parsedate_to_datetime(item.get("pubDate", "")).astimezone(KST).strftime("%Y-%m-%d %H:%M")
            except Exception:
                pub_date = ""

            article_data = {
                "idx": idx, "category": verify_and_adjust_category(category, title, desc),
                "title": title, "norm_t": norm_t, "description": desc, "link": link,
                "known_press": extract_press_from_link(link), "today_str": today_str,
                "pub_date": pub_date
            }
            all_articles.append(article_data)
            
            if norm_t not in unique_for_api:
                api_data = article_data.copy()
                api_data["description"] = desc[:80]
                unique_for_api[norm_t] = api_data
            idx += 1

    rows = []

    if SKIP_AI_ANALYSIS:
        print(f"[개발 모드] SKIP_AI_ANALYSIS=true → Gemini 호출 없이 {len(all_articles)}건을 원본 그대로 저장합니다.")
        for item in all_articles:
            group_title = force_merge_by_keywords(item["title"], normalize_title(item["title"]))
            rows.append([
                item["today_str"], item["category"], group_title, item["title"],
                item["known_press"] or "미상", "AI 분석 생략(개발 모드)", "미분석", 0, item["link"], item["pub_date"]
            ])
    else:
        # 완전 동일 제목(unique_for_api)에서 한 단계 더 나아가, 분야별로 제목이 비슷한 기사를 묶어
        # 그룹당 대표 기사 1건만 Gemini에 보낸다. 나머지는 API 호출 없이 대표의 결과를 그대로 나눠 쓴다.
        norm_titles_by_category = {}
        for nt, item in unique_for_api.items():
            norm_titles_by_category.setdefault(item["category"], []).append(nt)
        title_cluster_map = cluster_similar_titles(norm_titles_by_category)
        leader_norm_ts = list(dict.fromkeys(title_cluster_map.values()))

        api_items = []
        for i, nt in enumerate(leader_norm_ts):
            item = unique_for_api[nt].copy()
            item["idx"] = i
            api_items.append(item)
        api_items_by_idx = {item["idx"]: item for item in api_items}
        # 배치가 클수록 배치마다 반복되는 프롬프트 지시문 비용이 줄어든다 (너무 크면 응답 파싱 실패 위험)
        batches = [api_items[i:i + 20] for i in range(0, len(api_items), 20)]

        print(f"[비용 절감] 고유 제목 {len(unique_for_api)}건 → 유사 제목 클러스터링 후 {len(api_items)}건만 Gemini 분석")

        analyzed_results = {}

        for batch in batches:
            for r_idx, score, g_title, summary, sentiment, category in analyze_batch_with_gemini(batch):
                original_item = api_items_by_idx.get(r_idx)
                if not original_item:
                    continue
                g_title = force_merge_by_keywords(original_item["title"], g_title)
                final_category = category or original_item["category"]
                analyzed_results[original_item["norm_t"]] = (score, g_title, summary, sentiment, final_category)
            time.sleep(0.3)  # 유료 Tier 1은 분당 한도가 넉넉해 1초씩 쉴 필요 없음

        # 유사 제목 그룹의 나머지(대표가 아닌) 기사들도 대표와 같은 분석 결과를 그대로 사용
        for nt, leader_nt in title_cluster_map.items():
            if nt != leader_nt and leader_nt in analyzed_results:
                analyzed_results[nt] = analyzed_results[leader_nt]

        valid_group_titles = list(set([res[1] for res in analyzed_results.values() if res[0] >= 5 and res[1]]))
        if valid_group_titles:
            master_mapping = master_cluster_with_gemini(valid_group_titles)
            for norm_t, (score, orig_gt, summary, sentiment, category) in analyzed_results.items():
                if score >= 5 and orig_gt in master_mapping:
                    analyzed_results[norm_t] = (score, master_mapping[orig_gt], summary, sentiment, category)

        for item in all_articles:
            norm_t = item["norm_t"]
            if norm_t in analyzed_results:
                score, group_title, summary, sentiment, category = analyzed_results[norm_t]
                # 기술적 분석 실패("판단 실패")는 항상 노출하고, 저관련도(score<5)만 걸러낸다
                if sentiment != "판단 실패" and score < 5: continue
                group_title = force_merge_by_keywords(item["title"], group_title)
                category = category or item["category"]
            else:
                group_title, summary, sentiment, score = force_merge_by_keywords(item["title"], normalize_title(item["title"])), item["title"], "판단 실패", 0
                category = item["category"]

            rows.append([
                item["today_str"], category, group_title, item["title"],
                item["known_press"] or "미상", summary, sentiment, score, item["link"], item["pub_date"]
            ])

    save_and_merge_data(rows)

if __name__ == "__main__":
    main()
