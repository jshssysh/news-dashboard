"""법제처 Open API 응답 구조 확인용: 행정규칙, 별표·서식, 위임 행정규칙.

GitHub Actions "[점검] 법제처 API 응답 확인" 워크플로가 수동으로 돌려 로그만 본다.
회사 PC에서는 OC 키가 없어 응답을 볼 수 없다(데모키 test는 "IP 등록" 오류). 아무것도
저장·커밋하지 않는다.

법령 탭에 행정규칙(고시·훈령·예규)과 별표·서식을 붙이려면 먼저 아래를 확인해야 한다.
  1. target=admrul 목록: 공정위(org=1130000) 행정규칙에 어떤 칸이 오는가(종류·발령일·시행일·링크)
  2. target=admrul 본문: 제개정이유·상위법령·별표가 오는가
  3. target=lsDelegated: 법률 하나에 위임된 행정규칙 목록이 오는가(법령 카드와 잇는 데 씀)
  4. target=licbyl: 법령 별표·서식 목록(파일 링크)
  5. target=admbyl: 행정규칙 별표·서식 목록

실행: python probe_law_api.py [법령명]   (기본: 하도급거래 공정화에 관한 법률)
"""
import os
import sys
import xml.etree.ElementTree as ET

import requests

OC = os.environ.get("LAW_GO_KR_OC", "").strip() or "test"
BASE = "https://www.law.go.kr/DRF"
LAW_NAME = (sys.argv[1] if len(sys.argv) > 1 and sys.argv[1].strip() else "하도급거래 공정화에 관한 법률").strip()
RAW_CHARS = 1500


def call(path, **params):
    params = {"OC": OC, "type": "XML", **params}
    try:
        res = requests.get(f"{BASE}/{path}", params=params, timeout=30)
    except requests.exceptions.RequestException as e:
        print(f"[요청 실패] {e}")
        return None, ""
    text = res.text
    print(f"HTTP {res.status_code}, {len(text)}자, 요청 {path} {dict((k, v) for k, v in params.items() if k != 'OC')}")
    try:
        return ET.fromstring(text.encode("utf-8")), text
    except ET.ParseError:
        print("[XML 아님] 앞부분:", text[:RAW_CHARS])
        return None, text


def show_tree(root, depth=0, max_depth=3, max_children=40):
    """태그 구조를 들여쓰기로 찍는다(같은 태그가 여러 번이면 한 번만, 개수 표시)."""
    if root is None or depth > max_depth:
        return
    seen = {}
    for child in list(root):
        seen.setdefault(child.tag, []).append(child)
    for i, (tag, items) in enumerate(seen.items()):
        if i >= max_children:
            print("  " * depth + "  ...")
            break
        first = items[0]
        val = (first.text or "").strip().replace("\n", " ")
        cnt = f" x{len(items)}" if len(items) > 1 else ""
        print("  " * depth + f"- {tag}{cnt}: {val[:100]}")
        show_tree(first, depth + 1, max_depth, max_children)


def items(root, tag):
    return root.findall(f".//{tag}") if root is not None else []


def field(el, *names):
    for n in names:
        v = el.findtext(n)
        if v:
            return v.strip()
    return ""


def section(title):
    print("\n" + "=" * 70 + f"\n{title}\n" + "=" * 70)


def main():
    if OC == "test":
        print("[경고] LAW_GO_KR_OC가 없어 데모키(test)로 호출합니다.")

    section("0. 법령 ID 찾기 (target=law, 위임 행정규칙·별표 조회에 씀)")
    root, _ = call("lawSearch.do", target="law", query=LAW_NAME, display=10)
    law_id = law_mst = ""
    for el in items(root, "law"):
        name = field(el, "법령명한글", "법령명")
        print(f"  {name} | ID {field(el, '법령ID')} | MST {field(el, '법령일련번호')} | {field(el, '소관부처명')}")
        if name == LAW_NAME and not law_id:
            law_id, law_mst = field(el, "법령ID"), field(el, "법령일련번호")
    print(f"→ 쓸 법령: {LAW_NAME} ID={law_id} MST={law_mst}")

    section("1. 공정위 행정규칙 목록 (target=admrul, org=1130000)")
    root, raw = call("lawSearch.do", target="admrul", org="1130000", display=20, sort="ddes")
    if root is not None:
        print("totalCnt:", root.findtext("totalCnt"))
        rules = items(root, "admrul")
        if rules:
            print("첫 항목 구조:")
            show_tree(rules[0])
        for el in rules:
            print(f"  [{field(el, '행정규칙종류')}] {field(el, '행정규칙명')} | 발령 {field(el, '발령일자')} "
                  f"| 시행 {field(el, '시행일자')} | {field(el, '제개정구분명')} | 일련번호 {field(el, '행정규칙일련번호')} "
                  f"| ID {field(el, '행정규칙ID')}")
        if not rules:
            print(raw[:RAW_CHARS])
    first_rule = rules[0] if root is not None and rules else None

    section("2. 행정규칙 본문 (target=admrul, ID=첫 항목 일련번호)")
    if first_rule is not None:
        seq = field(first_rule, "행정규칙일련번호")
        root, raw = call("lawService.do", target="admrul", ID=seq)
        show_tree(root, max_depth=2)
        if root is not None:
            for tag in ["제개정이유", "제개정이유내용", "상위법령", "상위법령명", "별표", "부칙", "조문내용"]:
                found = root.findall(f".//{tag}")
                if found:
                    print(f"  <{tag}> {len(found)}개, 첫 내용: {(''.join(found[0].itertext())).strip()[:300]}")

    section(f"3. 위임 행정규칙 (target=lsDelegated, {LAW_NAME})")
    if law_id or law_mst:
        root, raw = call("lawService.do", target="lsDelegated", ID=law_id) if law_id else (None, "")
        if root is None and law_mst:
            root, raw = call("lawService.do", target="lsDelegated", MST=law_mst)
        show_tree(root, max_depth=4, max_children=15)
        if root is None:
            print(raw[:RAW_CHARS])

    section(f"4. 법령 별표·서식 (target=licbyl, {LAW_NAME})")
    for search in ["2", "1"]:  # 2: 해당법령으로 검색, 1: 별표서식명으로 검색(문서 기준)
        root, raw = call("lawSearch.do", target="licbyl", query=LAW_NAME, search=search, display=20)
        if root is None:
            continue
        print("totalCnt:", root.findtext("totalCnt"))
        rows = items(root, "licbyl")
        if rows:
            show_tree(rows[0])
            for el in rows[:20]:
                print(f"  [{field(el, '별표종류')}] {field(el, '별표명')} | {field(el, '관련법령명')} "
                      f"| 번호 {field(el, '별표번호')} | 파일 {field(el, '별표서식파일링크')[:80]}")
            break
        print(raw[:RAW_CHARS])

    section("5. 공정위 행정규칙 별표·서식 (target=admbyl, org=1130000)")
    root, raw = call("lawSearch.do", target="admbyl", org="1130000", display=10)
    if root is not None:
        print("totalCnt:", root.findtext("totalCnt"))
        rows = items(root, "admbyl")
        if rows:
            show_tree(rows[0])
            for el in rows[:10]:
                print(f"  [{field(el, '별표종류')}] {field(el, '별표명')} | {field(el, '관련행정규칙명')}")
        else:
            print(raw[:RAW_CHARS])

    section("6. 법령 본문 안의 별표 단위 (target=law, MST)")
    if law_mst:
        root, raw = call("lawService.do", target="law", MST=law_mst)
        units = items(root, "별표단위")
        print(f"별표단위 {len(units)}개")
        if units:
            show_tree(units[0], max_depth=1)
            for u in units[:15]:
                print(f"  [{field(u, '별표구분')}] {field(u, '별표번호')} {field(u, '별표제목')} "
                      f"| 파일 {field(u, '별표서식파일링크', '별표HWP파일명')[:80]} | PDF {field(u, '별표서식PDF파일링크')[:80]}")


if __name__ == "__main__":
    main()
