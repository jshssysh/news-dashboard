"""
법제처 국가법령정보 Open API에서 공정위 소관 법령(org=1130000) 전체 + 상법의
"벌칙/과징금/과태료/양벌규정" 조문과, 그 산정기준을 정하는 별표(시행령 별표 중
과징금·벌점 관련)를 뽑아 law_penalty_list.csv로 저장한다. 아울러 계약서 문구
검토(Cloudflare Worker) 기능이 참고할, 하도급·유통·가맹·대리점·약관·상법
계열 법령의 조문 전체(벌칙류로 안 좁힌 것)를 contract_law_corpus.csv로도
저장한다.

사용자가 감시하는 법령의 "지금 형량/과징금이 얼마인지"를 한눈에 보려는 목적이라,
전체 조문을 다 가져오는 대신 조문제목이 벌칙류 키워드를 담고 있는 조문만 골라낸다
(법령 초안 관례상 벌칙 조항은 거의 항상 "벌칙"/"과징금"/"과태료"/"양벌규정"으로
조문제목이 붙는다 - 실제로 하도급법 제29조~제31조로 확인함).

다만 "계약서 문구가 어느 조문에 저촉되는지" 판단하려면 벌칙 조문만으론
부족하다(실측 2026-09-14: "추가금액을 협력사가 전액 부담" 문구가 명백히
하도급법의 부당특약 금지에 걸리는데도, 벌칙류로 좁힌 목록에는 "부당한 특약의
금지" 같은 금지·의무 조항 자체가 아예 없어서 AI가 "저촉 조문 없음"으로 오판했다).
그래서 계약서 검토와 실제로 관련 있는 법령(CONTRACT_RELEVANT_LAW_ABBRS)에
한해서는 벌칙류 필터 없이 조문 전체를 별도 코퍼스로 함께 수집한다 - 범위를
이렇게 좁힌 이유는, 공정위 소관 법령 전체(34개)의 모든 조문을 다 넣으면
코퍼스가 너무 커져 AI 프롬프트에 다 못 넣기 때문(계약서 검토에 실제로
쓰이는 하도급·유통·가맹·대리점·약관·상법 계열만으로도 대부분의 B2B 계약
조항을 커버한다).

사용 API (www.law.go.kr/DRF, LAW_GO_KR_OC 필요 - collect_decisions.py와 동일):
- lawSearch.do?org=1130000: 공정거래위원회 소관 법령 전체(법률/시행령/시행규칙 등)를
  한 번에 가져온다. 사람이 목록을 손으로 관리할 필요가 없다.
- lawSearch.do?query=<법령명>: org 코드가 없는 상법처럼 별도로 추가하는 법령은
  이름으로 검색해 정확히 그 이름과 일치하는 것만 가져온다.
- lawService.do?target=law&MST=<법령일련번호>: 조문 전문을 XML로 받는다. 별표는
  <별표내용> CDATA로 텍스트가 같이 오므로(HWP/PDF/이미지 파일 링크와 별개로),
  대부분의 벌칙/과징금류 별표(산문 형태)는 이 텍스트만으로 파싱 가능하다 - 단,
  진짜 행/열 그리드 표는 고정폭 텍스트라 컬럼이 깨질 수 있어 여기서는 다루지 않는다.

조문시행일자(조문별 최신 개정일)를 "개정일"로 쓴다 - 법령 전체 공포일자보다 더
정확하다(한 법령 안에서도 조문마다 개정 시점이 다를 수 있으므로).

법 개정도 심·판결처럼 일주일에 몇 건 안 되므로(collect_decisions.py와 같은
이유), daily.yml처럼 하루 5번씩 돌 필요가 없다 - main()이 토요일 06시대
실행일 때만 실제로 수집하고 나머지 실행은 바로 종료한다(자세한 이유는 main()
안 주석 참고).

실행: python collect_law_penalties.py
"""
import os
import re
import time
import traceback
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import requests
import pandas as pd

LAW_API_OC = os.environ.get("LAW_GO_KR_OC", "").strip() or "test"
KST = timezone(timedelta(hours=9))

LAW_PENALTY_LIST_PATH = "law_penalty_list.csv"
CONTRACT_LAW_CORPUS_PATH = "contract_law_corpus.csv"
LAW_API_BASE = "https://www.law.go.kr/DRF"

FTC_ORG_CODE = "1130000"
# org 코드로 안 잡히는(공정위 소관이 아닌) 법령 중, 사용자가 추가로 감시하고
# 싶다고 밝힌 법령 - 이름으로 정확히 일치하는 것만 검색해서 추가한다.
EXTRA_LAW_NAMES = ["상법"]

# 조문제목에 이 단어가 들어있으면 "벌칙류 조문"으로 취급한다.
PENALTY_TITLE_KEYWORDS = ["벌칙", "과징금", "과태료", "양벌규정"]

# 계약서 문구 검토용 전체 조문 코퍼스에 포함할 법령 - B2B 계약(하도급/유통/
# 가맹/대리점)과 약관 일반을 커버한다. law["abbr"]와 정확히 일치해야 하므로
# fetch_ftc_laws()가 실제로 돌려주는 약칭 표기를 그대로 맞춘다.
# 상법은 뺐다 - 실측(2026-09-14) 결과 상법 조문만 1980건 중 1287건(65%)을
# 차지하는데, 회사법·보험·해상운송 등 계약서 검토와 무관한 내용이 대부분이라
# 코퍼스만 부풀리고 AI 프롬프트에 넣을 실효 내용은 오히려 희석시켰다.
CONTRACT_RELEVANT_LAW_ABBRS = {
    "하도급법", "하도급법 시행령",
    "대규모유통업법", "대규모유통업법 시행령",
    "대리점법", "대리점법 시행령",
    "가맹사업법", "가맹사업법 시행령",
    "공정거래법", "공정거래법 시행령",
    "약관법", "약관법 시행령",
}

# 계약서 검토 코퍼스에서 제외할, 순전히 행정적/절차적인 조문 제목(내용이
# "~하여서는 안 된다"류 실체 규정이 아니라 계약 조항과 대조할 의미가 없다).
ADMIN_TITLE_EXCLUDE_KEYWORDS = [
    "목적", "정의", "시행일", "다른 법률과의 관계", "권한의 위임", "권한의 재위임",
    "규제의 재검토", "고유식별정보의 처리", "벌칙 적용에서 공무원 의제",
    "과태료의 부과기준", "과징금의 부과기준", "수수료",
]

# 벌칙류 조문 중 실제 처벌 수위를 명시하는 문구를 뽑는다. 금액 표기가
# "2천만원"/"3억원"/"1억 5천만원"처럼 한글 단위가 섞여 있어 완전한 숫자 정규화는
# 하지 않고, 사람이 읽을 문구 그대로를 캡처한다.
IMPRISONMENT_PATTERN = re.compile(r"\d+년(?:\s*\d+월)?\s*이하의\s*징역")
FINE_PATTERN = re.compile(r"[0-9천만억\s]+원(?:\s*이하)?의\s*벌금")
ADMIN_FINE_PATTERN = re.compile(r"[0-9천만억\s]+원(?:\s*이하)?의\s*과태료")
# 과징금은 정액이 아니라 "매출액의 N배/N% 이내" 식 배율 규정이 많다.
PENALTY_SURCHARGE_PATTERN = re.compile(
    r"(?:하도급대금|매출액|거래금액|계약금액)의\s*[0-9]+(?:분의\s*[0-9]+|배)[^\.。]{0,20}(?:과징금|범위)"
    r"|과징금[^\.。]{0,40}(?:이내|넘지\s*못한다|초과하지\s*아니하는\s*범위)"
)


def get_law_api_with_retry(url, params, timeout=20, retries=2, retry_wait=10):
    """법제처 API 호출을 감싸서, 접속 실패(타임아웃 등)면 짧게 대기 후 재시도한다
    (collect_decisions.py의 동일 함수와 같은 목적)."""
    last_exc = None
    for attempt in range(retries + 1):
        try:
            res = requests.get(url, params=params, timeout=timeout)
            if res.status_code >= 500 and attempt < retries:
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


def fetch_ftc_laws():
    """공정거래위원회(org=1130000) 소관 법령 전체 목록을 가져온다."""
    params = {"OC": LAW_API_OC, "target": "law", "type": "XML", "org": FTC_ORG_CODE, "display": 100}
    res = get_law_api_with_retry(f"{LAW_API_BASE}/lawSearch.do", params)
    root = ET.fromstring(res.content)
    laws = []
    for law in root.findall(".//law"):
        laws.append({
            "mst": _t(law, "법령일련번호"),
            "name": _t(law, "법령명한글"),
            "abbr": _t(law, "법령약칭명") or _t(law, "법령명한글"),
            "kind": _t(law, "법령구분명"),
        })
    return laws


def fetch_named_laws(name):
    """이름으로 법령을 검색해, 법령명이 정확히 일치하는 것만 돌려준다
    (예: "상법"으로 검색하면 "상법", "상법 시행령" 등이 걸리는데 이름이 정확히
    "상법"인 것만 남긴다 - 이 함수는 EXTRA_LAW_NAMES 각각에 대해 한 번씩 불린다)."""
    # 이름 검색은 부분 문자열이 아니라 느슨한(자모 단위) 매칭이라, 흔한 두 글자
    # 조합("상법")은 관련 없는 법령("~보상법" 등)까지 수십 건 걸린다 - 정확히 일치하는
    # 것만 아래서 걸러내므로, 그 후보들이 다 담기도록 한 페이지(100건)를 통째로 본다.
    params = {"OC": LAW_API_OC, "target": "law", "type": "XML", "query": name, "display": 100}
    res = get_law_api_with_retry(f"{LAW_API_BASE}/lawSearch.do", params)
    root = ET.fromstring(res.content)
    laws = []
    for law in root.findall(".//law"):
        law_name = _t(law, "법령명한글")
        if law_name.replace(" ", "") != name.replace(" ", ""):
            continue
        laws.append({
            "mst": _t(law, "법령일련번호"),
            "name": law_name,
            "abbr": _t(law, "법령약칭명") or law_name,
            "kind": _t(law, "법령구분명"),
        })
    return laws


def _t(elem, path):
    child = elem.find(path)
    return (child.text or "").strip() if child is not None and child.text is not None else ""


def _full_text(elem):
    """조문단위/별표 element 안의 실제 본문 텍스트만 문서 순서대로 이어붙인다.
    태그 이름이 "...내용"으로 끝나는 것만 모은다(조문내용/항내용/호내용/목내용,
    별표내용) - elem.itertext()를 그냥 쓰면 조문번호/조문여부/조문시행일자 같은
    메타데이터 값까지 본문에 섞여 들어간다. 별표내용은 한 줄씩 고정폭 CDATA로
    쪼개져 있어 그대로 합치면 단어가 줄바꿈 중간에 끊기므로, 공백 하나로 이어붙이고
    연속 공백을 하나로 줄인다."""
    parts = []
    for e in elem.iter():
        if e.tag.endswith("내용") and e.text and e.text.strip():
            parts.append(e.text.strip())
    text = " ".join(parts)
    return re.sub(r"\s+", " ", text).strip()


def extract_matches(pattern, text):
    return " / ".join(dict.fromkeys(m.strip() for m in pattern.findall(text)))


def find_referenced_byls(article_text, byl_by_number):
    """조문 본문에 "별표 2", "별표 3의2" 같은 언급이 있으면, 같은 법령 문서 안에서
    번호가 일치하는 별표를 찾아 함께 반환한다 (조문이 과징금 산정을 별표로
    위임하는 경우 - 예: "과징금의 금액은 별표 2의 기준을 적용하여 산정한다")."""
    found = []
    for num in re.findall(r"별표\s*(\d+(?:의\s*\d+)?)", article_text):
        key = num.replace(" ", "")
        if key in byl_by_number and byl_by_number[key] not in found:
            found.append(byl_by_number[key])
    return found


def fetch_law_body(mst):
    """법령 전문을 받아 (벌칙류 조문 목록, 전체 조문 목록, 별표번호->별표텍스트
    사전)을 돌려준다. 두 조문 목록을 한 번의 API 호출로 같이 만들어서, 계약서
    검토 코퍼스(전체 조문)까지 필요해도 같은 법령을 두 번 안 받는다."""
    params = {"OC": LAW_API_OC, "target": "law", "MST": mst, "type": "XML"}
    res = get_law_api_with_retry(f"{LAW_API_BASE}/lawService.do", params)
    root = ET.fromstring(res.content)

    byl_by_number = {}
    for byl in root.findall(".//별표단위"):
        num = _t(byl, "별표번호")
        gaji = _t(byl, "별표가지번호")
        key = str(int(num)) if num.isdigit() else num
        if gaji and gaji not in ("0", "00"):
            key = f"{key}의{int(gaji)}" if gaji.isdigit() else f"{key}의{gaji}"
        title = _t(byl, "별표제목")
        text = _full_text(byl)
        byl_by_number[key] = {"제목": title, "본문": text}

    penalty_articles = []
    all_articles = []
    for unit in root.findall(".//조문단위"):
        title = _t(unit, "조문제목")
        num = _t(unit, "조문번호")
        gaji = _t(unit, "조문가지번호")
        label = f"제{num}조" + (f"의{gaji}" if gaji else "") + f"({title})"
        text = _full_text(unit)
        article = {
            "조문라벨": label,
            "조문제목": title,
            "조문시행일자": _t(unit, "조문시행일자"),
            "본문": text,
            "관련별표": find_referenced_byls(text, byl_by_number),
        }
        if any(kw in title for kw in PENALTY_TITLE_KEYWORDS):
            penalty_articles.append(article)
        if text and not any(kw in title for kw in ADMIN_TITLE_EXCLUDE_KEYWORDS):
            all_articles.append(article)
    return penalty_articles, all_articles


def classify_kind(title):
    if "과태료" in title:
        return "과태료"
    if "양벌규정" in title:
        return "양벌규정"
    if "과징금" in title:
        return "과징금"
    return "벌칙(형사처벌)"


def fetch_all_law_bodies(laws):
    """laws 각각의 (벌칙 조문, 전체 조문)을 한 번씩만 받아 {법령약칭: (...)}
    캐시로 돌려준다 - build_rows와 build_contract_corpus_rows가 이 캐시를
    나눠 쓰므로 겹치는 법령(하도급법 등)을 두 번 조회하지 않는다."""
    bodies = {}
    for i, law in enumerate(laws):
        if not law["mst"]:
            continue
        try:
            bodies[law["abbr"]] = fetch_law_body(law["mst"])
        except Exception:
            print(f"[법령 조회 실패] {law['name']} (MST={law['mst']}):\n{traceback.format_exc()}")
        if (i + 1) % 10 == 0:
            print(f"[법령 조회] {i + 1}/{len(laws)}건 처리")
        time.sleep(0.2)
    return bodies


def build_rows(laws, bodies):
    rows = []
    for law in laws:
        penalty_articles, _ = bodies.get(law["abbr"], ([], []))
        for art in penalty_articles:
            imprisonment = extract_matches(IMPRISONMENT_PATTERN, art["본문"])
            fine = extract_matches(FINE_PATTERN, art["본문"])
            admin_fine = extract_matches(ADMIN_FINE_PATTERN, art["본문"])
            surcharge = extract_matches(PENALTY_SURCHARGE_PATTERN, art["본문"])
            byl_titles = " / ".join(b["제목"] for b in art["관련별표"])
            byl_text = "\n\n".join(f"[{b['제목']}]\n{b['본문']}" for b in art["관련별표"])
            rows.append({
                "법령명": law["name"],
                "법령약칭": law["abbr"],
                "법령구분": law["kind"],
                "조문": art["조문라벨"],
                "유형": classify_kind(art["조문라벨"]),
                "개정일": art["조문시행일자"],
                "위반요건": art["본문"],
                "형벌_징역": imprisonment,
                "형벌_벌금": fine,
                "과태료": admin_fine,
                "과징금": surcharge,
                "산정기준별표": byl_titles,
                "산정기준상세": byl_text,
            })
    return rows


def build_contract_corpus_rows(laws, bodies):
    """계약서 검토용 전체 조문 코퍼스 - CONTRACT_RELEVANT_LAW_ABBRS에 든
    법령만, 벌칙류로 안 좁히고 조문 전체(행정적 조문 제외)를 담는다."""
    rows = []
    for law in laws:
        if law["abbr"] not in CONTRACT_RELEVANT_LAW_ABBRS:
            continue
        _, all_articles = bodies.get(law["abbr"], ([], []))
        for art in all_articles:
            rows.append({
                "법령명": law["name"],
                "법령약칭": law["abbr"],
                "조문": art["조문라벨"],
                "개정일": art["조문시행일자"],
                "조문내용": art["본문"],
            })
    return rows


def main():
    # 법 개정도 심·판결처럼 일주일에 몇 건 안 되므로(collect_decisions.py와 같은
    # 이유), daily.yml처럼 하루 5번 다 돌 필요가 없다 - 주 1회, 토요일 06:01
    # 실행 때만 실제로 수집하고 나머지 4번(같은 날 07:12/07:47/12:01/18:01)과
    # 평일은 건너뛴다. daily.yml 쪽 스텝 자체는 다른 스텝들과 똑같이 매번
    # 실행되지만(조건문을 워크플로에 따로 안 둬서 관리 포인트를 줄임), 여기서
    # 바로 종료하므로 API 호출은 실제로 주 1번만 나간다.
    # 지금 당장 한 번 돌리고 싶을 때는 LAW_PENALTIES_SKIP_WAIT=true로 위
    # 요일 제한을 건너뛸 수 있다(daily.yml의 skip_law_penalties_wait 입력을
    # 통해 넘어옴 - collect_decisions.py의 DECISIONS_SKIP_WAIT과 같은 규칙).
    skip_wait = os.environ.get("LAW_PENALTIES_SKIP_WAIT", "").strip().lower() == "true"
    now = datetime.now(KST)
    if not skip_wait and not (now.weekday() == 5 and now.hour == 6):
        print(f"[법령 벌칙/과징금 수집] 주 1회(토요일 06시대)만 실행하도록 정해둬서, "
              f"이번 실행({now.strftime('%a %H:%M')})은 건너뜁니다.")
        return

    if LAW_API_OC == "test":
        print("[경고] LAW_GO_KR_OC가 없어 데모키(test)로 호출합니다 - 정식 서비스에는 부적합합니다.")

    laws = fetch_ftc_laws()
    print(f"[공정위 소관 법령 목록] {len(laws)}건")
    for name in EXTRA_LAW_NAMES:
        extra = fetch_named_laws(name)
        print(f"[추가 법령 검색] '{name}' -> {[l['name'] for l in extra]}")
        laws.extend(extra)

    bodies = fetch_all_law_bodies(laws)
    now_str = datetime.now(KST).strftime("%Y-%m-%d %H:%M")

    rows = build_rows(laws, bodies)
    if not rows:
        print("[경고] 벌칙/과징금 조문을 하나도 못 찾았습니다 - law_penalty_list.csv를 덮어쓰지 않습니다.")
    else:
        for r in rows:
            r["최종수집일"] = now_str
        df = pd.DataFrame(rows)
        df.sort_values("개정일", ascending=False, inplace=True)
        df.to_csv(LAW_PENALTY_LIST_PATH, index=False, encoding="utf-8-sig")
        print(f"[법령 벌칙/과징금 수집 완료] {len(rows)}건 ({df['법령명'].nunique()}개 법령) -> {LAW_PENALTY_LIST_PATH}")

    corpus_rows = build_contract_corpus_rows(laws, bodies)
    if not corpus_rows:
        print("[경고] 계약서 검토 코퍼스를 하나도 못 만들었습니다 - contract_law_corpus.csv를 덮어쓰지 않습니다.")
        return
    corpus_df = pd.DataFrame(corpus_rows)
    corpus_df.sort_values(["법령약칭", "조문"], inplace=True)
    corpus_df.to_csv(CONTRACT_LAW_CORPUS_PATH, index=False, encoding="utf-8-sig")
    print(f"[계약서 검토 코퍼스 수집 완료] {len(corpus_rows)}건 ({corpus_df['법령약칭'].nunique()}개 법령) -> {CONTRACT_LAW_CORPUS_PATH}")


if __name__ == "__main__":
    main()
