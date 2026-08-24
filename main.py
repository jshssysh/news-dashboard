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
from urllib.parse import urlparse

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
    "ajunews.com": "아주경제", "asiatoday.co.kr": "아시아투데이",
    "intn.co.kr": "일간NTN"
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
# (사용자가 정리한 감시 분류표 기준 - config/keywords.yaml 상단 주석 참고)
CATEGORY_LIST = [
    "공정거래", "내부거래", "지배구조", "상생", "하도급", "상법",
    "시민단체", "노동", "기타",
    "그린·AI워싱", "삼성그룹", "삼성물산", "공정위인사",
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

def press_display_name(known_press, link):
    """언론사명을 못 찾은 경우, "미상" 대신 링크의 도메인을 보여준다."""
    if known_press:
        return known_press
    try:
        return urlparse(link).netloc.replace("www.", "") or "미상"
    except Exception:
        return "미상"

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
TITLE_SIMILARITY_THRESHOLD = 0.55  # 완화: 비용 절감 우선 (서로 다른 사건이 묶일 위험은 소폭 증가)

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
        else: return "공정거래"
    return category

PERSONNEL_CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config", "personnel.csv")

def extract_personnel_appointments(candidates):
    """'공정위인사' 카테고리로 잡힌 기사에서 (부서, 직책, 담당자, 발령일)을 추출한다.
    실제 과장급 이상 인사 발령 기사가 아니면 결과에서 제외된다."""
    if not GEMINI_API_KEY or not candidates:
        return []
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent?key={GEMINI_API_KEY}"
    input_data = [{"idx": i, "title": c["title"], "description": c["description"]} for i, c in enumerate(candidates)]
    prompt = f"""당신은 공정거래위원회 인사 발령 기사를 정리하는 담당자입니다.
입력 기사 목록: {json.dumps(input_data, ensure_ascii=False)}

각 기사에 대해, 공정거래위원회(본부 및 서울/부산/광주/대구/대전사무소 등 소속기관 포함) 소속
"과장급 이상"(과장, 팀장, 심의관, 국장, 처장, 사무소장, 관리관, 대변인, 사무처장, 위원장, 부위원장 등)
공무원의 인사 발령(임명·승진·전보)을 구체적인 이름과 함께 다루는 기사인지 판단하세요.

해당되면 다음 필드로 응답:
- is_appointment: true
- dept: 소속 부서명 (기사 원문 표현 그대로, 예: "사무처", "조사처", "서울사무소")
- role: 직책명 (예: "카르텔조사국장")
- name: 담당자 실명
- start_date: 발령일(YYYY-MM-DD), 기사에 날짜가 없으면 null

해당 안 되면(단순 동정 기사, 과장급 미만, 공정위 소속 아님, 이름이 불명확한 경우 등):
- is_appointment: false (다른 필드는 생략)

응답은 입력 기사 개수만큼, 각 항목에 idx를 포함해 아래 형식의 JSON 배열로만 출력하세요:
[
  {{"idx": 0, "is_appointment": true, "dept": "...", "role": "...", "name": "...", "start_date": "2026-08-20"}},
  {{"idx": 1, "is_appointment": false}}
]
"""
    # 하루 몇 건 안 되는 기사만 처리하는 호출이라 thinkingLevel을 low로 둬도 비용 부담이 적다
    payload = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"response_mime_type": "application/json", "thinkingConfig": {"thinkingLevel": "low"}}}
    try:
        res = requests.post(url, json=payload, timeout=30)
        if res.status_code == 200:
            res_json = res.json()
            log_gemini_usage(res_json, "공정위인사감지")
            raw_text = res_json['candidates'][0]['content']['parts'][0]['text'].strip()
            if raw_text.startswith("```json"): raw_text = raw_text[7:]
            if raw_text.startswith("```"): raw_text = raw_text[3:]
            if raw_text.endswith("```"): raw_text = raw_text[:-3]
            parsed_list = json.loads(raw_text.strip())
            results = []
            for item in parsed_list:
                if not item.get("is_appointment"):
                    continue
                i = item.get("idx")
                if i is None or not (0 <= i < len(candidates)):
                    continue
                dept = (item.get("dept") or "").strip()
                role = (item.get("role") or "").strip()
                name = (item.get("name") or "").strip()
                if not (dept and role and name):
                    continue
                results.append({
                    "dept": dept, "role": role, "name": name,
                    "start_date": item.get("start_date") or "",
                    "link": candidates[i]["link"],
                })
            return results
        else:
            print(f"[공정위 인사감지 오류] status={res.status_code} body={res.text[:300]}")
    except Exception as e:
        print(f"[공정위 인사감지 예외] {e}")
    return []

def update_personnel_csv(detected):
    """감지된 인사 발령을 config/personnel.csv에 추가한다.
    이미 같은 (부서, 직책, 담당자) 조합이 있으면 건너뛴다 - 사람이 검토 후
    확인상태를 "확인됨"으로 바꾸고 필요하면 이전 재임자의 종료일도 채워야 한다."""
    if not detected:
        return
    try:
        with open(PERSONNEL_CSV_PATH, "r", encoding="utf-8") as f:
            original_lines = f.readlines()
        df = pd.read_csv(PERSONNEL_CSV_PATH, comment="#")
    except Exception as e:
        print(f"[personnel.csv 로드 실패] {e}")
        return

    existing = set(zip(df["부서"], df["직책"], df["담당자"]))
    new_rows = []
    for d in detected:
        key = (d["dept"], d["role"], d["name"])
        if key in existing:
            continue
        new_rows.append({
            "부서": d["dept"], "직책": d["role"], "담당자": d["name"],
            "시작일": d["start_date"], "종료일": "",
            "출처링크": d["link"], "확인상태": "자동감지",
        })
        existing.add(key)

    if not new_rows:
        return

    updated = pd.concat([df, pd.DataFrame(new_rows)], ignore_index=True)
    comment_lines = [line for line in original_lines if line.startswith("#")]
    with open(PERSONNEL_CSV_PATH, "w", encoding="utf-8", newline="") as f:
        f.writelines(comment_lines)
        updated.to_csv(f, index=False)
    print(f"[공정위 인사 자동 감지] {len(new_rows)}건 personnel.csv에 추가 (확인상태=자동감지, 검토 필요)")

def force_merge_by_keywords(title, original_group_title):
    t_lower = title.replace(" ", "")
    if "RX사업추진실" in t_lower or ("대표이사직속" in t_lower and "로봇" in t_lower):
        return "삼성전자 RX사업추진실 신설"
    return original_group_title

def log_gemini_usage(res_json, label):
    """비용 확인용: 호출마다 입력/출력/thinking 토큰 수를 GitHub Actions 로그에 남긴다."""
    usage = res_json.get("usageMetadata", {})
    prompt_t = usage.get("promptTokenCount", 0)
    output_t = usage.get("candidatesTokenCount", 0)
    thoughts_t = usage.get("thoughtsTokenCount", 0)
    total_t = usage.get("totalTokenCount", 0)
    print(f"[Gemini 토큰 사용량:{label}] 입력={prompt_t} 출력={output_t} thinking={thoughts_t} 합계={total_t}")

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
    # thinkingBudget=0: 단순 분류/추출 작업이라 긴 추론이 불필요한데, 기본값(동적 사고)으로 두면
    # 눈에 안 보이는 "thinking" 토큰이 출력 토큰 요금(입력의 8배 이상)으로 과금되어 비용이 커진다
    payload = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"response_mime_type": "application/json", "thinkingConfig": {"thinkingLevel": "low"}}}

    try:
        res = requests.post(url, json=payload, timeout=30)
        if res.status_code == 200:
            res_json = res.json()
            log_gemini_usage(res_json, "batch분석")
            raw_text = res_json['candidates'][0]['content']['parts'][0]['text'].strip()
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

def load_recent_issue_titles(file_name="news_list.csv", days=14, limit=80):
    """최근 N일간 이미 저장된 대표이슈명을 반환한다 (등장 빈도 높은 순으로 최대 limit개).
    여러 날에 걸쳐 보도되는 사건이 매일 실행되는 클러스터링에서 서로 다른 이슈명으로
    쪼개지는 것을 막기 위해, master_cluster_with_gemini가 '기존 이슈명'을 참고할 수 있게 한다."""
    if not os.path.exists(file_name):
        return []
    try:
        df = pd.read_csv(file_name)
        cutoff = datetime.now(KST).replace(tzinfo=None) - timedelta(days=days)
        dt = pd.to_datetime(df["수집일자"], errors="coerce")
        recent = df.loc[dt >= cutoff, "대표이슈"].dropna()
        return recent.value_counts().head(limit).index.tolist()
    except Exception as e:
        print(f"[최근 이슈명 로드 예외] {e}")
        return []

def master_cluster_with_gemini(new_titles, existing_titles=None):
    existing_titles = existing_titles or []
    if not GEMINI_API_KEY or not new_titles: return {title: title for title in new_titles}
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent?key={GEMINI_API_KEY}"
    prompt = f"""당신은 뉴스 이슈 클러스터링 전문가입니다.
[오늘 새로 발견된 이슈명]
{json.dumps(new_titles, ensure_ascii=False)}
[최근 14일간 이미 사용 중인 기존 이슈명] (참고용)
{json.dumps(existing_titles, ensure_ascii=False)}

병합 기준: 등장하는 기업명/기관명이 "같고" 다루는 사건(예: 같은 소송, 같은 제재 처분, 같은 정책 발표)이
사실상 동일하면, 이슈명의 표현(예: "확정" vs "패소" vs "규제" vs "전가")이 서로 달라도 같은 사건으로 보고 병합하세요.
예: "GS리테일 과징금 확정", "GS리테일 판촉비 전가", "GS리테일 과징금 패소", "공정위 규제"가 모두 같은 대법원 판결을
다루고 있다면 전부 하나의 이슈로 병합해야 합니다.

주의: "담합", "과징금", "제재" 같은 사건 유형(카테고리)이 같다는 이유만으로 병합하면 안 됩니다.
반드시 등장하는 기업/기관명이 일치해야 병합 대상입니다. 예: "정유사 담합"과 "삼겹살 카르텔"은 둘 다
담합 사건이지만 서로 다른 업종·기업의 별개 사건이므로 병합하지 마세요.

작업 순서:
1. 먼저 '오늘 새로 발견된 이슈명' 안에서 서로 같은 사건을 다루는 항목들을 하나로 묶으세요.
2. 그렇게 묶인(또는 단독인) 각 사건에 대해, '기존 이슈명' 중 같은 사건을 다루는 것이 있다면
   반드시 새 이름을 만들지 말고 그 기존 이슈명을 merged 값으로 그대로 재사용하세요.
   (여러 날에 걸쳐 보도되는 사건이 매일 다른 이슈명으로 쪼개지는 것을 막기 위함입니다.)
3. 일치하는 기존 이슈명이 없을 때만 새로운 '통합 대표 이슈명(10자 이내 명사형)'을 만드세요.

응답은 '오늘 새로 발견된 이슈명' 각 항목에 대해서만, 아래 형식의 JSON 배열로 출력하세요:
[
  {{"original": "오늘이슈명1", "merged": "재사용된 기존 이슈명 또는 새 이슈명"}}
]
"""
    # 이 호출은 하루 1번, 짧은 이슈명 목록만 처리하므로 thinkingLevel을 올려도 비용 영향이 미미하다.
    # (반면 analyze_batch_with_gemini는 배치마다 반복 호출되므로 low를 유지해 비용을 억제한다.)
    # "같은 사건, 다른 표현"을 알아보는 의미적 추론이 필요한 단계라 low로는 병합 누락이 잦았다.
    payload = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"response_mime_type": "application/json", "thinkingConfig": {"thinkingLevel": "medium"}}}
    try:
        res = requests.post(url, json=payload, timeout=30)
        if res.status_code == 200:
            res_json = res.json()
            log_gemini_usage(res_json, "이슈통합")
            raw_text = res_json['candidates'][0]['content']['parts'][0]['text'].strip()
            if raw_text.startswith("```json"): raw_text = raw_text[7:]
            if raw_text.startswith("```"): raw_text = raw_text[3:]
            if raw_text.endswith("```"): raw_text = raw_text[:-3]
            parsed_list = json.loads(raw_text.strip())
            mapping = {item.get("original", ""): item.get("merged", "") for item in parsed_list}
            # 응답에서 빠진 항목은 원래 이름을 그대로 유지 (부분 실패가 전체를 깨지 않도록)
            for t in new_titles:
                mapping.setdefault(t, t)
            return mapping
        else:
            print(f"[Gemini 클러스터링 오류] status={res.status_code} body={res.text[:300]}")
    except Exception as e:
        print(f"[Gemini 클러스터링 예외] {e}")
    return {title: title for title in new_titles}

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
        combined_df["dt"] = pd.to_datetime(combined_df["수집일자"], format="%Y-%m-%d %H:%M", errors="coerce", utc=True)
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
                press_display_name(item["known_press"], item["link"]), "AI 분석 생략(개발 모드)", "미분석", 0, item["link"], item["pub_date"]
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
            time.sleep(4.5)  # 무료 등급은 분당 15회 제한 (60/15=4초) - 여유 있게 4.5초씩 대기

        # 유사 제목 그룹의 나머지(대표가 아닌) 기사들도 대표와 같은 분석 결과를 그대로 사용
        for nt, leader_nt in title_cluster_map.items():
            if nt != leader_nt and leader_nt in analyzed_results:
                analyzed_results[nt] = analyzed_results[leader_nt]

        valid_group_titles = list(set([res[1] for res in analyzed_results.values() if res[0] >= 5 and res[1]]))
        if valid_group_titles:
            recent_existing_titles = [t for t in load_recent_issue_titles() if t not in valid_group_titles]
            master_mapping = master_cluster_with_gemini(valid_group_titles, recent_existing_titles)
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
                press_display_name(item["known_press"], item["link"]), summary, sentiment, score, item["link"], item["pub_date"]
            ])

    if not SKIP_AI_ANALYSIS:
        # 대시보드 관련도 점수와 무관하게, 키워드 단계에서 이미 '공정위인사'로 잡힌 기사만
        # 대상으로 한다 (all_articles 기준 - rows에 들어가기 전 단계라 관련도 필터링의 영향을 안 받음)
        personnel_candidates = [
            {"title": a["title"], "description": a["description"], "link": a["link"]}
            for a in all_articles if a["category"] == "공정위인사"
        ]
        detected = extract_personnel_appointments(personnel_candidates)
        update_personnel_csv(detected)

    save_and_merge_data(rows)

if __name__ == "__main__":
    main()
