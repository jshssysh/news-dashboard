"""
현직 국회의원마다 네이버 뉴스에서 관련 기사를 검색해 member_news.csv에 누적한다.
member_list.csv(collect_bills.py가 만듦)가 먼저 있어야 하므로 그 뒤에 실행한다.

검색어는 "{이름} 의원"으로 좁혀서 동명이인 기사 노출을 줄인다(완벽하진 않음).
같은 사건을 여러 언론사가 그대로 받아쓴 기사는 정규화한 제목으로 묶어 대표
기사 하나만 남기고("관련보도수"로 나머지 개수만 기록), 이미 저장돼 있는
기사와 제목이 같으면(재보도) 다시 추가하지 않는다. 과거 이슈 이력을 계속
보고 싶다는 요청이라 사람당 상한 없이 전부 누적한다(news_list.csv/
bill_list.csv와 같은 방식 - 무한정 쌓이지만 하루 증분만 새로 API를 태운다).

요약은 Gemini로 1문장만 받는다 - 법안 요약(collect_bills.py)과 같은 방식으로
배치+서킷브레이커를 쓴다. 이미 요약이 있는 기사(재실행)는 다시 요약하지 않는다.

실행: python collect_member_news.py
"""
import json
import os
import re
import time
from datetime import timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import pandas as pd
import requests

NAVER_CLIENT_ID = os.environ.get("NAVER_CLIENT_ID", "").strip().replace('"', '').replace("'", "")
NAVER_CLIENT_SECRET = os.environ.get("NAVER_CLIENT_SECRET", "").strip().replace('"', '').replace("'", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip().replace('"', '').replace("'", "")
KST = timezone(timedelta(hours=9))

MEMBER_LIST_PATH = "member_list.csv"
OUT_PATH = "member_news.csv"
OUT_COLUMNS = ["의원명", "제목", "언론사", "링크", "발행일", "요약", "관련보도수"]
DISPLAY_PER_QUERY = 20


def load_active_member_names():
    """의장단은 포함하되(위원회만 없을 뿐 현직), 다른 자리로 옮겼거나(현직변경)
    확정판결로 의원직을 잃은(의원직상실) 사람은 뺀다 - html_template.html의
    isActiveMember()와 같은 기준."""
    if not os.path.exists(MEMBER_LIST_PATH):
        return []
    try:
        df = pd.read_csv(MEMBER_LIST_PATH).fillna("")
    except Exception:
        return []
    active = df[(df["현직변경"] == "") & (df["의원직상실"] == "")]
    return [n for n in active["이름"].tolist() if n]


def clean_text(text):
    return (text or "").replace("<b>", "").replace("</b>", "").replace("&quot;", '"').replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")


def normalize_title(title):
    """같은 사건을 여러 언론사가 그대로 받아쓴 기사를 하나로 묶기 위한 비교용 키.
    대괄호 태그([속보], [단독] 등)와 공백/구두점을 지운다."""
    t = re.sub(r"<[^>]+>", "", title or "")
    t = clean_text(t)
    t = re.sub(r"\[[^\]]*\]", "", t)
    t = re.sub(r"[^\w가-힣]", "", t)
    return t.strip()


def press_display_name(link):
    try:
        return urlparse(link).netloc.replace("www.", "") or "미상"
    except Exception:
        return "미상"


def search_member_news(name):
    headers = {"X-Naver-Client-Id": NAVER_CLIENT_ID, "X-Naver-Client-Secret": NAVER_CLIENT_SECRET}
    query = f"{name} 의원"
    url = f"https://openapi.naver.com/v1/search/news.json?query={query}&display={DISPLAY_PER_QUERY}&sort=date"
    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            return res.json().get("items", [])
        print(f"[의원 뉴스 검색 오류] {name}: status={res.status_code}")
    except Exception as e:
        print(f"[의원 뉴스 검색 예외] {name}: {e}")
    return []


def post_gemini_with_retry(url, payload, timeout=30, retries=1, retry_wait=5):
    last_exc = None
    for attempt in range(retries + 1):
        try:
            res = requests.post(url, json=payload, timeout=timeout)
            if res.status_code == 503 and attempt < retries:
                time.sleep(retry_wait)
                continue
            return res
        except requests.exceptions.RequestException as e:
            last_exc = e
            if attempt < retries:
                time.sleep(retry_wait)
                continue
            raise
    raise last_exc


def summarize_member_news_with_gemini(items):
    """[{"idx","name","title","description"}] -> {idx: 요약}. 법안 요약과 같은
    배치(20건)+서킷브레이커(연속 3회 실패 시 포기) 방식."""
    if not GEMINI_API_KEY or not items:
        return {}
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent?key={GEMINI_API_KEY}"
    results = {}
    consecutive_failures = 0
    batches = [items[i:i + 20] for i in range(0, len(items), 20)]
    for batch in batches:
        if consecutive_failures >= 3:
            print(f"[의원 뉴스 요약] 연속 {consecutive_failures}회 실패 - 남은 배치는 다음 실행으로 미룸")
            break
        input_data = [{"idx": it["idx"], "member": it["name"], "title": it["title"], "description": it["description"]} for it in batch]
        prompt = f"""당신은 국회의원 관련 기사를 한 줄로 정리하는 담당자입니다.
입력 기사 목록: {json.dumps(input_data, ensure_ascii=False)}

각 기사가 해당 의원(member)과 관련해 어떤 이슈/소식인지 1문장으로 요약하세요.
"이 의원은/그는" 같은 표현 없이 사실만 바로 쓰세요.
신문 헤드라인처럼 짧고 간결하게 쓰세요(20자 안팎) - "~하는 과정에서", "~하는 도중",
"~문제로", "~와 관련하여" 같은 배경 설명·연결어는 다 빼고 핵심 사실만 남기세요.
예(좋음): "성평등가족부 장관 후보자로 지명", "자료 제출 시점 두고 여당과 설전"
예(나쁨 - 너무 길고 풀어씀): "국회 상임위원회 회의 도중 자료로 제출 시점 문제로
여당 의원들과 설전을 벌임"
기사가 그 의원과 실제로는 무관하면(동명이인 등) summary를 빈 문자열로 두세요.

응답은 입력 개수만큼, 각 항목에 idx를 포함해 아래 형식의 JSON 배열로만 출력하세요:
[
  {{"idx": 0, "summary": "..."}}
]
"""
        payload = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"response_mime_type": "application/json", "thinkingConfig": {"thinkingLevel": "low"}}}
        try:
            res = post_gemini_with_retry(url, payload)
            if res.status_code == 200:
                consecutive_failures = 0
                res_json = res.json()
                raw_text = res_json['candidates'][0]['content']['parts'][0]['text'].strip()
                if raw_text.startswith("```json"): raw_text = raw_text[7:]
                if raw_text.startswith("```"): raw_text = raw_text[3:]
                if raw_text.endswith("```"): raw_text = raw_text[:-3]
                for item in json.loads(raw_text.strip()):
                    if not isinstance(item, dict):
                        continue
                    i = item.get("idx")
                    if i is not None:
                        results[i] = (item.get("summary") or "").strip()
            else:
                consecutive_failures += 1
                print(f"[의원 뉴스 요약 오류] status={res.status_code} body={res.text[:300]}")
        except Exception as e:
            consecutive_failures += 1
            print(f"[의원 뉴스 요약 예외] {e}")
    return results


def main():
    if not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET:
        print("[의원 뉴스] NAVER_CLIENT_ID/SECRET이 없어 건너뜁니다.")
        return

    names = load_active_member_names()
    if not names:
        print("[의원 뉴스] member_list.csv에서 현직 의원을 찾지 못해 종료합니다.")
        return
    print(f"[의원 뉴스] 현직 {len(names)}명 검색 시작")

    if os.path.exists(OUT_PATH):
        existing_df = pd.read_csv(OUT_PATH).fillna("")
    else:
        existing_df = pd.DataFrame(columns=OUT_COLUMNS)

    existing_titles_by_member = {}
    for name, group in existing_df.groupby("의원명"):
        existing_titles_by_member[name] = set(normalize_title(t) for t in group["제목"])

    new_rows = []
    summarize_items = []
    for i, name in enumerate(names):
        items = search_member_news(name)
        seen_norm = set()
        for item in items:
            title = clean_text(item.get("title", ""))
            # 언론사가 붙이는 "[가상자산 부활]" 같은 연재/코너 태그는 내용과 무관해서 화면에
            # 그대로 노출하면 잡음이 된다 - 제목 맨 앞의 대괄호 태그만 떼어낸다.
            title = re.sub(r"^\[[^\]]*\]\s*", "", title).strip()
            norm = normalize_title(title)
            if not norm or norm in seen_norm:
                continue  # 이번 검색 결과 안에서의 중복(같은 사건 재보도)
            seen_norm.add(norm)
            if norm in existing_titles_by_member.get(name, set()):
                continue  # 예전에 이미 저장해둔 것과 같은 사건
            related_count = sum(
                1 for other in items
                if normalize_title(clean_text(other.get("title", ""))) == norm
            )
            try:
                pub_date = parsedate_to_datetime(item.get("pubDate", "")).astimezone(KST).strftime("%Y-%m-%d")
            except Exception:
                pub_date = ""
            link = item.get("originallink") or item.get("link") or ""
            row_idx = len(new_rows)
            new_rows.append({
                "의원명": name,
                "제목": title,
                "언론사": press_display_name(link),
                "링크": link,
                "발행일": pub_date,
                "요약": "",
                "관련보도수": related_count,
            })
            summarize_items.append({
                "idx": row_idx, "name": name, "title": title,
                "description": clean_text(item.get("description", "")),
            })
        if (i + 1) % 50 == 0:
            print(f"[의원 뉴스] {i + 1}/{len(names)}명 검색 완료")
        time.sleep(0.1)

    print(f"[의원 뉴스] 신규(중복 제외) {len(new_rows)}건 발견 - 요약 시작")
    summary_map = summarize_member_news_with_gemini(summarize_items)
    for it in summarize_items:
        new_rows[it["idx"]]["요약"] = summary_map.get(it["idx"], "")

    if new_rows:
        combined = pd.concat([existing_df, pd.DataFrame(new_rows, columns=OUT_COLUMNS)], ignore_index=True)
    else:
        combined = existing_df

    if combined.empty:
        pd.DataFrame(columns=OUT_COLUMNS).to_csv(OUT_PATH, index=False, encoding="utf-8-sig")
        print("[의원 뉴스] 저장할 기사가 없습니다.")
        return

    # 과거에 무슨 이슈가 있었는지 계속 남겨두고 싶다는 요청이라, 사람당 상한 없이
    # 전부 누적한다(중복은 이미 위에서 걸렀으니 여기선 정렬만).
    combined = combined.sort_values("발행일", ascending=False)
    combined.to_csv(OUT_PATH, index=False, encoding="utf-8-sig")
    print(f"[의원 뉴스 저장 완료] 총 {len(combined)}건 -> {OUT_PATH}")


if __name__ == "__main__":
    main()
