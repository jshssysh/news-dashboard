"""
현직 국회의원마다 네이버 뉴스에서 관련 기사를 검색해 member_news.csv에 누적한다.
member_list.csv(collect_bills.py가 만듦)가 먼저 있어야 하므로 그 뒤에 실행한다.

검색어는 "{이름} 의원"으로 좁혀서 동명이인 기사 노출을 줄인다(완벽하진 않음).
검색 자체는 아무 기사나 다 걸리므로(지역구 행사 방문, 축하 인사 등), Gemini로
"기업제재 / 국정감사 / 의원인사 / 법안발의 / 종교후원행사" 5가지 중 하나에
해당하는지 걸러서 그중 하나로 분류된 것만 저장한다 - 그 외(주최 측이 안
드러나는 단순 동정 등)나 동명이인으로 실제 무관한 기사는 아예 저장하지 않는다.

같은 사건을 여러 언론사가 그대로 받아쓴 기사는 정규화한 제목으로 묶어 대표
기사 하나만 남기고("관련보도수"로 나머지 개수만 기록), 이미 저장돼 있는
기사와 제목이 같으면(재보도) 다시 추가하지 않는다. 과거 이슈 이력을 계속
보고 싶다는 요청이라 사람당 상한 없이 전부 누적한다(news_list.csv/
bill_list.csv와 같은 방식 - 무한정 쌓이지만 하루 증분만 새로 API를 태운다).

분류·요약은 Gemini로 한 번에 받는다 - 법안 요약(collect_bills.py)과 같은
배치+서킷브레이커 방식. 5가지 분류 밖이거나 API 실패로 분류를 못 받은 기사는
저장되지 않고 다음 실행에서 다시 검색·재시도된다(제목만으로 "이미 처리됨"을
판단하므로, 저장된 기사는 전부 분류를 통과한 것들이다).

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


# 이 5가지에 해당하는 기사만 저장한다(그 외 - 단순 지역구 행사, 축하 인사, 동정,
# SNS 논란 등 - 는 이 프로젝트가 다루는 공정거래·입법 동향과 무관해서 뺀다).
# "종교/후원단체 행사"만 예외적으로 남기는 이유: 단순 동정이 아니라 특정 종교단체·
# 후원(기부) 관계가 있는 단체와의 유대 관계 자체가 추적할 가치가 있는 정보라서다.
RELEVANT_CATEGORIES = {"기업제재", "국정감사", "의원인사", "법안발의", "종교후원행사"}


def classify_and_summarize_with_gemini(items):
    """[{"idx","name","title","description"}] -> {idx: {"category","summary"}}.
    법안 요약과 같은 배치(20건)+서킷브레이커(연속 3회 실패 시 포기) 방식."""
    if not GEMINI_API_KEY or not items:
        return {}
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent?key={GEMINI_API_KEY}"
    results = {}
    consecutive_failures = 0
    batches = [items[i:i + 20] for i in range(0, len(items), 20)]
    for batch in batches:
        if consecutive_failures >= 3:
            print(f"[의원 뉴스 분류] 연속 {consecutive_failures}회 실패 - 남은 배치는 다음 실행으로 미룸")
            break
        input_data = [{"idx": it["idx"], "member": it["name"], "title": it["title"], "description": it["description"]} for it in batch]
        prompt = f"""당신은 국회의원 관련 기사를 분류하고 한 줄로 정리하는 담당자입니다.
입력 기사 목록: {json.dumps(input_data, ensure_ascii=False)}

각 기사가 해당 의원(member)과 관련해 아래 5가지 중 하나에 해당하는지 판단하세요:
- 기업제재: 공정위 등 정부의 기업 제재·조사·처분에 그 의원이 관여(질의·비판·촉구 등)한 소식
- 국정감사: 국정감사·상임위 회의에서 그 의원의 질의·발언 활동
- 의원인사: 그 의원 자신의 보직 변경·임명·해임·사퇴 등 신상 변화
- 법안발의: 그 의원이 대표발의·공동발의한 법안 소식
- 종교후원행사: 특정 종교단체(교회·사찰·성당 등)가 주최한 행사, 또는 그 의원에게
  기부·후원하거나 그 의원이 후원하는 단체가 주최한 행사에 참석·축사 등을 한 소식
  (어느 종교/단체인지, 어떤 관계인지 알 수 있어야 함 - 그냥 "지역 행사 참석"처럼
  주최 측이 안 드러나는 일반 동정은 여기 포함하지 말고 무관으로 처리)

이 5가지 중 하나에 해당하면 category를 그 이름 그대로 쓰고, summary는 신문
헤드라인처럼 짧게(20자 안팎) 쓰세요. "이 의원은/그는" 같은 표현이나 "~하는
과정에서", "~하는 도중", "~문제로" 같은 배경 설명·연결어는 다 빼고 핵심
사실만 남기세요. 종교후원행사는 요약에 단체명이 드러나게 쓰세요(예: "여의도순복음교회
행사서 축사", "OO장학재단 후원의 밤 참석").
예(좋음): "성평등가족부 장관 후보자로 지명", "자료 제출 시점 두고 여당과 설전"
예(나쁨 - 너무 길고 풀어씀): "국회 상임위원회 회의 도중 자료로 제출 시점 문제로
여당 의원들과 설전을 벌임"

5가지 어디에도 해당하지 않으면(주최 측이 안 드러나는 단순 지역구 행사·방문,
일반 축하 인사, 동정, SNS 논란 등) 또는 기사가 그 의원과 실제로 무관하면
(동명이인 등) category를 "무관"으로, summary는 빈 문자열로 두세요.

응답은 입력 개수만큼, 각 항목에 idx를 포함해 아래 형식의 JSON 배열로만 출력하세요:
[
  {{"idx": 0, "category": "...", "summary": "..."}}
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
                        results[i] = {
                            "category": (item.get("category") or "무관").strip(),
                            "summary": (item.get("summary") or "").strip(),
                        }
            else:
                consecutive_failures += 1
                print(f"[의원 뉴스 분류 오류] status={res.status_code} body={res.text[:300]}")
        except Exception as e:
            consecutive_failures += 1
            print(f"[의원 뉴스 분류 예외] {e}")
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

    # 저장되는 기사는 전부 아래에서 4가지 분류를 통과한 것들뿐이라, 제목만 봐도
    # "이미 처리된 기사"로 판단해도 안전하다(분류에서 떨어진 기사는 애초에 저장 안 됨).
    existing_titles_by_member = {}
    for name, group in existing_df.groupby("의원명"):
        existing_titles_by_member[name] = set(normalize_title(t) for t in group["제목"])

    # 검색 결과 자체는 아무 기사나 다 걸리므로, 일단 후보로만 모아두고 저장 여부는
    # Gemini 분류(4가지 카테고리) 결과를 본 뒤에 결정한다.
    candidates = []
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
            candidates.append({
                "idx": len(candidates),
                "name": name,
                "title": title,
                "description": clean_text(item.get("description", "")),
                "press": press_display_name(link),
                "link": link,
                "date": pub_date,
                "relatedCount": related_count,
            })
        if (i + 1) % 50 == 0:
            print(f"[의원 뉴스] {i + 1}/{len(names)}명 검색 완료")
        time.sleep(0.1)

    print(f"[의원 뉴스] 신규(중복 제외) 후보 {len(candidates)}건 발견 - 분류/요약 시작")
    result_map = classify_and_summarize_with_gemini(candidates)

    new_rows = []
    kept_by_category = {}
    for c in candidates:
        r = result_map.get(c["idx"])
        if not r or r["category"] not in RELEVANT_CATEGORIES or not r["summary"]:
            continue  # 분류 대상 밖(무관)이거나, 실패해서 분류를 못 받음 -> 다음 실행에서 재시도
        kept_by_category[r["category"]] = kept_by_category.get(r["category"], 0) + 1
        new_rows.append({
            "의원명": c["name"],
            "제목": c["title"],
            "언론사": c["press"],
            "링크": c["link"],
            "발행일": c["date"],
            "요약": r["summary"],
            "관련보도수": c["relatedCount"],
        })
    breakdown = ", ".join(f"{k} {v}건" for k, v in kept_by_category.items()) if kept_by_category else "해당 없음"
    print(f"[의원 뉴스] 분류 통과 {len(new_rows)}건 저장 ({breakdown})")

    if new_rows:
        combined = pd.concat([existing_df, pd.DataFrame(new_rows, columns=OUT_COLUMNS)], ignore_index=True)
    else:
        combined = existing_df

    if combined.empty:
        pd.DataFrame(columns=OUT_COLUMNS).to_csv(OUT_PATH, index=False, encoding="utf-8-sig")
        print("[의원 뉴스] 저장할 기사가 없습니다.")
        return

    # 과거에 무슨 이슈가 있었는지 계속 남겨두고 싶다는 요청이라, 사람당 상한 없이
    # 전부 누적한다.
    combined = combined.sort_values("발행일", ascending=False)
    combined.to_csv(OUT_PATH, index=False, encoding="utf-8-sig")
    print(f"[의원 뉴스 저장 완료] 총 {len(combined)}건 -> {OUT_PATH}")


if __name__ == "__main__":
    main()
