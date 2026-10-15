"""정답 데이터(eval/labels_*.csv)로 지금의 Gemini 채점 기준을 시험한다.

GitHub Actions의 "뉴스 필터 평가" 워크플로가 실행한다(회사 PC에는 Gemini 키가 없음).
뉴스 데이터는 건드리지 않고, 결과만 eval/results/latest.md(요약)와 latest.csv(기사별
점수)로 저장한다 - 워크플로가 이 두 파일만 커밋한다.

실제 수집과 같은 조건이 되도록 네이버 검색으로 각 기사의 설명을 다시 받아 제목과 함께
채점한다(정답 데이터에는 제목만 있음). 오래된 기사는 검색에 안 잡혀 설명 없이 채점될 수
있고, 그 수는 결과에 따로 적는다.

Gemini 호출: 200건 / 20건씩 = 약 10회(평가를 돌릴 때만).
"""
import glob
import os
import re
import sys
import time
import urllib.parse

import pandas as pd
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import main  # noqa: E402

HIDDEN = {"삼성그룹", "삼성물산", "공정위인사"}
THRESHOLDS = [6, 7]
RESULTS_DIR = os.path.join(HERE, "results")


def fetch_description(title, link):
    """네이버 뉴스 검색에서 이 기사(링크 일치)의 설명을 찾는다. 못 찾으면 ""."""
    if not main.NAVER_CLIENT_ID:
        return ""
    query = re.sub(r"\[[^\]]*\]|\.{2,}|…$", " ", str(title)).strip()
    url = ("https://openapi.naver.com/v1/search/news.json?display=30&sort=sim&query="
           + urllib.parse.quote(query))
    headers = {"X-Naver-Client-Id": main.NAVER_CLIENT_ID, "X-Naver-Client-Secret": main.NAVER_CLIENT_SECRET}
    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code != 200:
            return ""
        for item in res.json().get("items", []):
            if link in (item.get("originallink"), item.get("link")):
                return main.clean_text(item.get("description", ""))[:main.ANALYZE_DESCRIPTION_CHARS]
    except requests.exceptions.RequestException:
        pass
    return ""


def row_report(name, show, is_y):
    shown = int(show.sum())
    noise = int((show & ~is_y).sum())
    missed = int((~show & is_y).sum())
    return (f"| {name} | {shown} | {noise} ({noise / max(shown, 1):.0%}) | "
            f"{missed} ({missed / max(int(is_y.sum()), 1):.0%}) |")


def main_eval():
    if not main.GEMINI_API_KEY:
        sys.exit("GEMINI_API_KEY가 없습니다 - GitHub Actions에서 실행하세요.")
    path = sorted(glob.glob(os.path.join(HERE, "labels_*.csv")))[-1]
    labels = pd.read_csv(path, encoding="utf-8-sig")

    descriptions = []
    for _, r in labels.iterrows():
        descriptions.append(fetch_description(r["제목"], r["기사링크"]))
        time.sleep(0.1)
    labels["설명"] = descriptions
    found = int((labels["설명"] != "").sum())

    items = [
        {"idx": i, "title": r["제목"], "description": r["설명"], "category": r["분야(당시)"]}
        for i, r in labels.iterrows()
    ]
    scores, cats = {}, {}
    for start in range(0, len(items), 20):
        for idx, score, _title, _summary, _sentiment, category in main.analyze_batch_with_gemini(items[start:start + 20]):
            scores[idx], cats[idx] = score, category
        time.sleep(4.5)
    labels["새 점수"] = labels.index.map(lambda i: scores.get(i, 0))
    labels["새 분야"] = labels.index.map(lambda i: cats.get(i) or labels.at[i, "분야(당시)"])

    # 화면에서 숨겨지는 삼성 분야는 노출 판정에서 뺀다
    old_view = labels[~labels["분야(당시)"].isin(HIDDEN)]
    new_view = labels[~labels["새 분야"].isin(HIDDEN)]
    lines = [
        f"## 뉴스 필터 평가 ({os.path.basename(path)}, {len(labels)}건)",
        "",
        f"- 실행: {main.datetime.now(main.KST).strftime('%Y-%m-%d %H:%M')} KST",
        f"- 네이버에서 설명을 찾은 기사: {found}/{len(labels)}건 (나머지는 제목만으로 채점)",
        f"- Gemini 호출: {main.GEMINI_USAGE['calls']}회 (429 {main.GEMINI_USAGE['rate_limited']}회)",
        "",
        "| 기준 | 화면 기사 | 잡음(나오면 안 되는 기사) | 놓침(나와야 하는데 빠진 기사) |",
        "|---|---|---|---|",
        row_report("당시 점수, 5점 이상", old_view["중요도(당시)"] >= 5, old_view["노출"] == "Y"),
        row_report("당시 점수, 6점 이상", old_view["중요도(당시)"] >= 6, old_view["노출"] == "Y"),
    ]
    for t in THRESHOLDS:
        lines.append(row_report(f"새 기준, {t}점 이상", new_view["새 점수"] >= t, new_view["노출"] == "Y"))
    t = THRESHOLDS[0]
    wrong = new_view[(new_view["새 점수"] >= t) != (new_view["노출"] == "Y")]
    lines += ["", f"### 새 기준 {t}점에서 틀린 기사 ({len(wrong)}건)", "",
              "| 정답 | 새 점수 | 당시 점수 | 설명 | 제목 |", "|---|---|---|---|---|"]
    for _, r in wrong.iterrows():
        has_desc = "있음" if r["설명"] else "없음"
        lines.append(f"| {r['노출']} | {r['새 점수']} | {r['중요도(당시)']} | {has_desc} | "
                     f"{str(r['제목']).replace('|', '/')} |")
    text = "\n".join(lines)
    print(text)

    os.makedirs(RESULTS_DIR, exist_ok=True)
    with open(os.path.join(RESULTS_DIR, "latest.md"), "w", encoding="utf-8") as f:
        f.write(text + "\n")
    labels[["기사링크", "제목", "노출", "중요도(당시)", "새 점수", "새 분야"]].assign(
        설명찾음=labels["설명"] != ""
    ).to_csv(os.path.join(RESULTS_DIR, "latest.csv"), index=False, encoding="utf-8-sig")
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write(text + "\n")


if __name__ == "__main__":
    main_eval()
