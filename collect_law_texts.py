"""법령 탭 "조문 검색"과 행정규칙·별표서식 목록용 자료를 법제처 Open API에서 모은다.

법령 탭에서 "공시 과태료"처럼 찾으면 공정거래법 제130조(과태료), 시행령 과태료 부과기준
별표, 관련 고시 조문이 같이 나오게 하려고(2026-11-04 사용자 요청) 현행 조문 전문을 받아 둔다.
결과는 law_texts.json 하나(저장소에 커밋, 다음 실행의 캐시로도 씀) - generate_html.py가 이를
화면용 docs/law_meta.json(행정규칙·별표서식 목록, 작음)과 docs/law_texts.json(조문 검색용,
검색할 때만 받음)으로 나눈다.

대상
- 법령: 공정위 소관 현행 법령(org=1130000) + collect_subordinate_laws.OTHER_MINISTRY_LAWS
  (법률·시행령·시행규칙). 법령 탭의 법령 목록과 같은 범위.
- 행정규칙: 공정위 소관 현행 행정규칙 중 훈령을 뺀 것(고시·예규·지침 등). 훈령은 인사관리·
  예산·보안처럼 내부 운영 규정이 대부분이라 기업이 지킬 기준이 아니다(2026-11-04 확인 목록 기준).

사용 API (www.law.go.kr/DRF, LAW_GO_KR_OC 필요). 응답 구조는 probe_law_api.py로 확인했다
(2026-11-04, eval/results/probe_law_api.txt).
- lawSearch.do?target=law&org=1130000 / &query=법령명: 현행 법령 목록(법령일련번호=MST)
- lawService.do?target=law&MST=: 조문단위(조문번호·가지번호·조문제목·…내용), 별표단위(별표내용)
- lawSearch.do?target=licbyl&query=법령명&search=2: 법령 별표·서식 목록(파일 링크)
- lawSearch.do?target=admrul&org=1130000: 행정규칙 목록(행정규칙일련번호·종류·발령·시행일자)
- lawService.do?target=admrul&ID=일련번호: 조문내용 여러 개, 제개정이유내용, 첨부파일
- lawSearch.do?target=admbyl&org=1130000: 행정규칙 별표·서식 목록(관련행정규칙일련번호로 잇는다)

본문은 개정되면 일련번호(MST·행정규칙일련번호)가 바뀌므로, 같은 번호는 다시 받지 않고 지난
law_texts.json 것을 쓴다. 별표·서식 목록도 그 법령 묶음에 새 번호가 없으면 지난 것을 쓴다.
법령 공포·시행 수집(collect_subordinate_laws.py)과 같은 시각(06시대 실행)에만 돈다.

실행: python collect_law_texts.py
"""
import json
import os
import re
import time
import traceback
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import requests

import paths  # 저장소 안 파일 경로(paths.py)

from collect_subordinate_laws import OTHER_MINISTRY_LAWS, EXCLUDE_NAME_KEYWORDS

LAW_API_OC = os.environ.get("LAW_GO_KR_OC", "").strip() or "test"
LAW_API_BASE = "https://www.law.go.kr/DRF"
SITE = "https://www.law.go.kr"
KST = timezone(timedelta(hours=9))
FTC_ORG_CODE = "1130000"
LAW_TEXTS_PATH = paths.LAW_TEXTS
RULE_EXCLUDE_KINDS = {"훈령"}
CALL_GAP = 0.2


def get_xml(path, **params):
    """법제처 API를 불러 XML 루트를 돌려준다. 빈 본문·5xx·접속 실패는 두 번까지 다시 시도."""
    params = {"OC": LAW_API_OC, "type": "XML", **params}
    last = None
    for attempt in range(3):
        try:
            res = requests.get(f"{LAW_API_BASE}/{path}", params=params, timeout=30)
            if res.status_code < 500 and res.content.strip():
                time.sleep(CALL_GAP)
                return ET.fromstring(res.content)
            last = f"HTTP {res.status_code}, {len(res.content)}바이트"
        except (requests.exceptions.RequestException, ET.ParseError) as e:
            last = repr(e)
        time.sleep(10)
    raise RuntimeError(f"{path} {params.get('target')} 호출 실패: {last}")


def _t(elem, path):
    child = elem.find(path)
    return (child.text or "").strip() if child is not None and child.text else ""


def _text(elem):
    """태그 이름이 "...내용"으로 끝나는 것만 문서 순서대로 잇는다(collect_law_penalties._full_text와 같다).
    메타데이터(조문번호·시행일자 등)가 본문에 섞이지 않게 한다."""
    parts = [e.text.strip() for e in elem.iter() if e.tag.endswith("내용") and e.text and e.text.strip()]
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


# 표 테두리 문자(─┬┃ 등)는 검색에 쓸모가 없고 별표 크기만 키운다.
BOX_CHARS = re.compile(r"[─-╿■-◿]+")


def slim_byl(b):
    """서식(빈 신청서 양식)은 본문을 버리고 제목만 남긴다 - 2026-11-04 첫 수집에서 서식 본문이
    전체 조문 자료 1,100만 자 중 240만 자였는데 빈칸·안내문뿐이라 검색에 쓸모가 없었다.
    별표는 표 테두리 문자만 걷어 낸다."""
    if b.get("kind") == "서식":
        return {**b, "text": ""}
    return {**b, "text": re.sub(r"\s+", " ", BOX_CHARS.sub(" ", b.get("text", ""))).strip()}


def _name_key(name):
    return re.sub(r"[\s·ㆍ・]", "", name or "")


def _link(path):
    return SITE + path if path and path.startswith("/") else (path or "")


# ── 법령 ────────────────────────────────────────────────────────────────
def list_current_laws():
    """현행 법령 목록 {MST: {...}} - 공정위 소관 전부 + 다른 부처는 정한 법령과 그 시행령·시행규칙만."""
    laws = {}

    def add(el):
        name = _t(el, "법령명한글")
        if any(k in name for k in EXCLUDE_NAME_KEYWORDS):
            return
        mst = _t(el, "법령일련번호")
        if mst:
            laws[mst] = {"name": name, "abbr": _t(el, "법령약칭명"), "kind": _t(el, "법령구분명"),
                         "ministry": _t(el, "소관부처명"), "id": _t(el, "법령ID")}

    for page in range(1, 6):
        items = get_xml("lawSearch.do", target="law", org=FTC_ORG_CODE, display=100, page=page).findall(".//law")
        for el in items:
            add(el)
        if len(items) < 100:
            break
    for base in OTHER_MINISTRY_LAWS:
        wanted = {_name_key(base), _name_key(base + " 시행령"), _name_key(base + " 시행규칙")}
        for el in get_xml("lawSearch.do", target="law", query=base, display=100).findall(".//law"):
            if _name_key(_t(el, "법령명한글")) in wanted:
                add(el)
    return laws


def fetch_law_body(mst):
    root = get_xml("lawService.do", target="law", MST=mst)
    articles = []
    for unit in root.findall(".//조문단위"):
        if _t(unit, "조문여부") == "전문":  # 장·절 제목 줄
            continue
        num, gaji, title = _t(unit, "조문번호"), _t(unit, "조문가지번호"), _t(unit, "조문제목")
        text = _text(unit)
        if not num or not text:
            continue
        articles.append({"no": f"제{num}조" + (f"의{gaji}" if gaji and gaji != "0" else ""), "title": title, "text": text})
    byls = []
    for b in root.findall(".//별표단위"):
        num, gaji = _t(b, "별표번호"), _t(b, "별표가지번호")
        label = str(int(num)) if num.isdigit() else num
        if gaji and gaji.strip("0"):
            label += f"의{int(gaji)}" if gaji.isdigit() else f"의{gaji}"
        text = _text(b)
        if text:
            byls.append(slim_byl({"no": label, "kind": _t(b, "별표구분") or "별표", "title": _t(b, "별표제목"), "text": text}))
    # 현행 본문의 부칙은 제정 때부터의 부칙이 다 붙어 있어 가장 이른 부칙 공포일이 제정일이다
    # (단, 전부개정한 법은 그 전 부칙이 빠질 수 있어 first_promulgation()과 같이 본다).
    dates = [d for d in ((e.text or "").strip() for e in root.iter("부칙공포일자")) if len(d) == 8 and d.isdigit()]
    return articles, byls, (min(dates) if dates else "")


def first_promulgation(name, law_id):
    """법령 연혁 목록(target=eflaw, nw=1 연혁)에서 같은 법령ID의 가장 이른 공포일자. 법령 탭을
    "오래된 법부터" 보이려고 받는다(2026-11-05 사용자 요청). 못 찾으면 ""."""
    root = get_xml("lawSearch.do", target="eflaw", query=name, nw=1, sort="dasc", display=100)
    dates = [_t(el, "공포일자") for el in root.findall(".//law")
             if (not law_id or _t(el, "법령ID") == law_id) and _name_key(_t(el, "법령명한글")) == _name_key(name)]
    dates = [d for d in dates if len(d) == 8 and d.isdigit()]
    return min(dates) if dates else ""


def enacted_date(name, law_id, buchik_first):
    """제정일 = 연혁 목록의 가장 이른 공포일과 부칙의 가장 이른 공포일 중 이른 쪽(YYYY-MM-DD)."""
    try:
        hist = first_promulgation(name, law_id)
    except Exception:
        print(f"[연혁 조회 실패] {name}:\n{traceback.format_exc()}")
        hist = ""
    cands = [d for d in (hist, buchik_first) if d]
    return _fmt(min(cands)) if cands else ""


def list_law_byl_files(base_name):
    """법령 이름으로 별표·서식 목록(파일 링크)을 받는다 - 시행령·시행규칙 것도 같이 온다."""
    files = []
    for page in range(1, 6):
        items = get_xml("lawSearch.do", target="licbyl", query=base_name, search=2, display=100, page=page).findall(".//licbyl")
        for el in items:
            files.append({"law": _t(el, "관련법령명"), "mst": _t(el, "관련법령일련번호"), "no": _t(el, "별표번호"),
                          "kind": _t(el, "별표종류"), "title": _t(el, "별표명"),
                          "file": _link(_t(el, "별표서식파일링크")), "pdf": _link(_t(el, "별표서식PDF파일링크"))})
        if len(items) < 100:
            break
    return files


# ── 행정규칙 ────────────────────────────────────────────────────────────
def list_rules():
    rules = {}
    for page in range(1, 11):
        items = get_xml("lawSearch.do", target="admrul", org=FTC_ORG_CODE, display=100, page=page).findall(".//admrul")
        for el in items:
            kind = _t(el, "행정규칙종류")
            if kind in RULE_EXCLUDE_KINDS or (_t(el, "현행연혁구분") and _t(el, "현행연혁구분") != "현행"):
                continue
            seq = _t(el, "행정규칙일련번호")
            rules[seq] = {"name": _t(el, "행정규칙명"), "kind": kind, "ministry": _t(el, "소관부처명"),
                          "issued": _fmt(_t(el, "발령일자")), "number": _t(el, "발령번호"),
                          "effective": _fmt(_t(el, "시행일자")), "revision": _t(el, "제개정구분명")}
        if len(items) < 100:
            break
    return rules


def fetch_rule_body(seq):
    root = get_xml("lawService.do", target="admrul", ID=seq)
    articles = []
    for el in root.findall(".//조문내용"):
        text = re.sub(r"\s+", " ", "".join(el.itertext())).strip()
        if not text:
            continue
        m = re.match(r"(제\d+조(?:의\d+)?)\s*(?:\(([^)]*)\))?", text)
        articles.append({"no": m.group(1) if m else "", "title": (m.group(2) or "") if m else "", "text": text})
    reason = "\n".join(x.strip() for x in ("".join(e.itertext()) for e in root.findall(".//제개정이유내용")) if x.strip())
    reason = re.sub(r"\n\s*\n+", "\n", reason).strip()
    attach = [{"title": (f.text or "").strip(), "file": ""} for f in root.findall(".//첨부파일명")]
    for a, f in zip(attach, root.findall(".//첨부파일링크")):
        a["file"] = (f.text or "").strip()
    return articles, reason, attach


def list_rule_byl_files():
    by_rule = {}
    for page in range(1, 21):
        items = get_xml("lawSearch.do", target="admbyl", org=FTC_ORG_CODE, display=100, page=page).findall(".//admrulbyl")
        for el in items:
            by_rule.setdefault(_t(el, "관련행정규칙일련번호"), []).append({
                "no": _t(el, "별표번호"), "kind": _t(el, "별표종류"), "title": _t(el, "별표명"),
                "file": _link(_t(el, "별표서식파일링크")), "pdf": _link(_t(el, "별표서식PDF파일링크"))})
        if len(items) < 100:
            break
    for files in by_rule.values():
        files.sort(key=lambda f: f["no"])
    return by_rule


def _fmt(d):
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 and d.isdigit() else d


def load_cache():
    try:
        with open(LAW_TEXTS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def main():
    skip_wait = os.environ.get("SUBORDINATE_LAWS_SKIP_WAIT", "").strip().lower() == "true"
    now = datetime.now(KST)
    if not skip_wait and now.hour != 6:
        print(f"[법령 조문·행정규칙 수집] 하루 1번(06시대)만 실행 - 이번 실행({now.strftime('%a %H:%M')})은 건너뜁니다.")
        return
    if LAW_API_OC == "test":
        print("[경고] LAW_GO_KR_OC가 없어 데모키(test)로 호출합니다.")

    cache = load_cache()
    old_laws, old_rules = cache.get("laws", {}), cache.get("rules", {})
    old_files = cache.get("lawFiles", {})
    stats = {"law_new": 0, "law_fail": 0, "rule_new": 0, "rule_fail": 0, "files_new": 0}

    try:
        current = list_current_laws()
    except Exception:
        print(f"[법령 목록 조회 실패] 기존 파일을 그대로 둡니다.\n{traceback.format_exc()}")
        return
    if not current:
        print("[경고] 현행 법령 목록이 비었습니다(키·IP 오류일 수 있음) - law_texts.json을 덮어쓰지 않습니다.")
        return
    laws = {}
    for mst, meta in current.items():
        # 제정일("enacted")이 없던 예전 수집분은 한 번만 본문을 다시 받아 채운다.
        if mst in old_laws and old_laws[mst].get("articles") and "enacted" in old_laws[mst]:
            laws[mst] = {**old_laws[mst], **meta, "byls": [slim_byl(b) for b in old_laws[mst].get("byls", [])]}
            continue
        try:
            articles, byls, buchik_first = fetch_law_body(mst)
            laws[mst] = {**meta, "articles": articles, "byls": byls,
                         "enacted": enacted_date(meta["name"], meta.get("id", ""), buchik_first)}
            stats["law_new"] += 1
        except Exception:
            stats["law_fail"] += 1
            print(f"[법령 본문 실패] {meta['name']} (MST {mst}):\n{traceback.format_exc()}")

    # 별표·서식 파일 목록은 법률 이름 묶음(법률 + 그 시행령·시행규칙)으로 받는다.
    groups = {}
    for mst, law in laws.items():
        base = re.sub(r"\s*시행(령|규칙)$", "", law["name"])
        groups.setdefault(base, set()).add(mst)
    law_files = {}
    for base, msts in groups.items():
        sig = ",".join(sorted(msts))
        if base in old_files and old_files[base].get("sig") == sig:
            law_files[base] = old_files[base]
            continue
        try:
            law_files[base] = {"sig": sig, "files": list_law_byl_files(base)}
            stats["files_new"] += 1
        except Exception:
            print(f"[별표·서식 목록 실패] {base}:\n{traceback.format_exc()}")
            if base in old_files:
                law_files[base] = old_files[base]

    rules = {}
    try:
        rule_list = list_rules()
        rule_files = list_rule_byl_files()
    except Exception:
        print(f"[행정규칙 목록 조회 실패] 지난 행정규칙을 그대로 씁니다.\n{traceback.format_exc()}")
        rule_list, rule_files, rules = {}, None, old_rules
    for seq, meta in rule_list.items():
        old = old_rules.get(seq)
        if old and (old.get("articles") or old.get("reason")):
            rules[seq] = {**old, **meta}
        else:
            try:
                articles, reason, attach = fetch_rule_body(seq)
                rules[seq] = {**meta, "articles": articles, "reason": reason, "attach": attach}
                stats["rule_new"] += 1
            except Exception:
                stats["rule_fail"] += 1
                print(f"[행정규칙 본문 실패] {meta['name']} ({seq}):\n{traceback.format_exc()}")
                continue
        if rule_files is not None:
            rules[seq]["files"] = rule_files.get(seq, [])

    # 내용이 그대로면 파일을 다시 쓰지 않는다 - 수십 MB 파일이라 시각만 바뀐 채 매일 커밋되면
    # 저장소가 금방 불어난다. "updated"는 내용이 실제로 바뀐 시각이다.
    out = {"laws": laws, "rules": rules, "lawFiles": law_files}
    if {k: cache.get(k) for k in out} == out:
        print("[법령 조문·행정규칙 수집] 바뀐 내용이 없어 law_texts.json을 그대로 둡니다.")
        return
    out = {"updated": now.strftime("%Y-%m-%d %H:%M"), **out}
    with open(LAW_TEXTS_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    n_art = sum(len(x.get("articles", [])) for x in laws.values())
    n_byl = sum(len(x.get("byls", [])) for x in laws.values())
    n_rart = sum(len(x.get("articles", [])) for x in rules.values())
    print(f"[법령 조문·행정규칙 수집 완료] 법령 {len(laws)}개(새로 {stats['law_new']}, 실패 {stats['law_fail']}) "
          f"조문 {n_art} 별표 {n_byl} / 행정규칙 {len(rules)}개(새로 {stats['rule_new']}, 실패 {stats['rule_fail']}) "
          f"조문 {n_rart} / 별표·서식 목록 새로 {stats['files_new']}묶음 / {os.path.getsize(LAW_TEXTS_PATH) / 1024:.0f}KB")


if __name__ == "__main__":
    main()
