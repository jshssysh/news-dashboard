"""정답 데이터(eval/labels_*.csv 전부)로 뉴스 필터를 시험한다.

GitHub Actions의 "뉴스 필터 평가" 워크플로가 실행한다(회사 PC에는 Gemini 키가 없음).
뉴스 데이터는 건드리지 않고, 결과만 eval/results/latest.md(요약)와 latest.csv(기사별
점수)로 저장한다 - 워크플로가 이 두 파일만 커밋한다.

1) 채점: 정답 데이터를 만든 날의 점수(당시)와 지금 main.py 지시문으로 다시 매긴 점수를
   같은 기사로 비교한다. 층별 표본(층·층 모집단 열이 있는 파일)은 층 크기로 가중해 전체
   수집분 기준 비율로 바꾼다 - 탈락 기사까지 넣어야 "예전엔 버려지던 기사가 올라오는"
   경우를 볼 수 있다(10/14 정답 데이터만으로 평가했다가 이걸 놓쳤음).
2) 묶기: 노출 Y 기사들을 임베딩 유사도로 묶어, 정답의 '사건'과 얼마나 맞는지 지금
   방식(당시 대표이슈)과 비교한다.

실제 수집과 같은 조건이 되도록 네이버 검색으로 기사 설명을 다시 받아 채점·임베딩한다.
"""
import glob
import itertools
import os
import re
import sys
import time
import urllib.parse

import numpy as np
import pandas as pd
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import main  # noqa: E402

HIDDEN = {"삼성그룹", "삼성물산", "공정위인사"}
SHOW_MIN = 6  # generate_html.DISPLAY_MIN_ISSUE_SCORE와 같은 기준(여기선 기사 단위)
GROUP_THRESHOLDS = [0.70, 0.75, 0.80, 0.85, 0.88, 0.90, 0.92, 0.95]
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
                return main.clean_text(item.get("description", ""))
    except requests.exceptions.RequestException:
        pass
    return ""


def rescore(labels):
    items = [
        {"idx": i, "title": r["제목"], "description": r["설명"][:main.ANALYZE_DESCRIPTION_CHARS],
         "category": r["분야(당시)"]}
        for i, r in labels.iterrows()
    ]
    scores, cats = {}, {}
    for start in range(0, len(items), 20):
        for idx, score, _t, _s, _sent, category in main.analyze_batch_with_gemini(items[start:start + 20]):
            scores[idx], cats[idx] = score, category
        time.sleep(4.5)
    labels["새 점수"] = labels.index.map(lambda i: scores.get(i, 0))
    labels["새 분야"] = labels.index.map(lambda i: cats.get(i) or labels.at[i, "분야(당시)"])


def weights(labels):
    """층별 표본이면 기사 1건이 대표하는 모집단 기사 수, 아니면 1."""
    if "층" in labels.columns and "층 모집단" in labels.columns:
        n = labels.groupby("층")["층"].transform("size")
        return labels["층 모집단"] / n
    return pd.Series(1.0, index=labels.index)


def score_row(name, labels, score_col, cat_col):
    view = labels[~labels[cat_col].isin(HIDDEN)]
    w = weights(labels).loc[view.index]
    show = view[score_col] >= SHOW_MIN
    is_y = view["노출"] == "Y"
    shown = w[show].sum()
    noise = w[show & ~is_y].sum()
    missed = w[~show & is_y].sum()
    total_y = w[is_y].sum()
    return (f"| {name} | {shown:.0f} | {noise / max(shown, 1e-9):.0%} | "
            f"{missed / max(total_y, 1e-9):.0%} |")


def pair_errors(groups, events):
    """같은 사건인데 다른 묶음(나뉨), 다른 사건인데 같은 묶음(섞임) 쌍의 비율."""
    same_ev = split = same_g = merge = 0
    for a, b in itertools.combinations(range(len(events)), 2):
        if events[a] == events[b]:
            same_ev += 1
            split += groups[a] != groups[b]
        if groups[a] == groups[b]:
            same_g += 1
            merge += events[a] != events[b]
    return split / max(same_ev, 1), merge / max(same_g, 1), same_ev, same_g


def greedy_cluster(vectors, threshold):
    """앞에서부터 기사마다 가장 비슷한 묶음의 중심과 코사인 유사도가 threshold 이상이면
    거기에 넣고, 아니면 새 묶음을 만든다(실제 수집처럼 순서대로 붙이는 방식)."""
    centroids, sums, groups = [], [], []
    for v in vectors:
        best, best_sim = None, -1.0
        for gi, c in enumerate(centroids):
            sim = float(np.dot(v, c))
            if sim > best_sim:
                best, best_sim = gi, sim
        if best is not None and best_sim >= threshold:
            sums[best] = sums[best] + v
            centroids[best] = sums[best] / np.linalg.norm(sums[best])
            groups.append(best)
        else:
            sums.append(v.copy())
            centroids.append(v.copy())
            groups.append(len(centroids) - 1)
    return groups


def grouping_report(name, labels, model):
    rel = labels[(labels["노출"] == "Y") & (labels["사건"].astype(str).str.strip() != "")].reset_index(drop=True)
    if len(rel) < 2:
        return []
    events = rel["사건"].tolist()
    lines = [f"#### {name} (노출 Y 기사 {len(rel)}건, 사건 {rel['사건'].nunique()}개)", "",
             "| 방식 | 나뉨(같은 사건인데 갈림) | 섞임(다른 사건인데 묶임) | 묶음 수 |", "|---|---|---|---|"]
    cur = rel["대표이슈(당시)"].fillna("").tolist()
    s, m, _, _ = pair_errors(cur, events)
    lines.append(f"| 지금 방식(이슈명) | {s:.0%} | {m:.0%} | {len(set(cur))} |")
    if not model:
        return lines + ["", "임베딩 모델을 찾지 못해 비교하지 못했습니다.", ""]
    texts = [f"{r['제목']} {r['설명']}".strip() for _, r in rel.iterrows()]
    raw = main.embed_texts(texts, model)
    if any(v is None for v in raw):
        return lines + ["", f"임베딩 실패 {sum(v is None for v in raw)}건 - 비교하지 못했습니다.", ""]
    vecs = np.array(raw, dtype=float)
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
    for t in GROUP_THRESHOLDS:
        g = greedy_cluster(vecs, t)
        s, m, _, _ = pair_errors(g, events)
        lines.append(f"| 임베딩, 유사도 {t:.2f} 이상 | {s:.0%} | {m:.0%} | {len(set(g))} |")
    return lines + [""]


def main_eval():
    if not main.GEMINI_API_KEY:
        sys.exit("GEMINI_API_KEY가 없습니다 - GitHub Actions에서 실행하세요.")
    paths = sorted(glob.glob(os.path.join(HERE, "labels_*.csv")))
    model = main.pick_embedding_model()
    lines = [
        "## 뉴스 필터 평가",
        "",
        f"- 실행: {main.datetime.now(main.KST).strftime('%Y-%m-%d %H:%M')} KST",
        f"- 임베딩 모델: {model or '없음'}",
        "",
        "### 1. 채점 (같은 기사, 당시 점수 vs 지금 지시문)",
        "",
        f"기준: 기사 단위 {SHOW_MIN}점 이상이면 화면 노출. 층별 표본은 층 크기로 가중한 전체 수집분 기준 비율.",
        "",
    ]
    group_lines = ["### 2. 묶기 (임베딩 vs 지금 방식)", ""]
    per_article = []
    for path in paths:
        name = os.path.basename(path)
        labels = pd.read_csv(path, encoding="utf-8-sig")
        labels["사건"] = labels["사건"].fillna("")
        descs = []
        for _, r in labels.iterrows():
            descs.append(fetch_description(r["제목"], r["기사링크"]))
            time.sleep(0.1)
        labels["설명"] = descs
        rescore(labels)
        found = int((labels["설명"] != "").sum())
        lines += [
            f"#### {name} ({len(labels)}건, 설명 찾음 {found}건)", "",
            "| 기준 | 화면 기사(가중) | 잡음 | 놓침 |", "|---|---|---|---|",
            score_row("당시 점수", labels, "중요도(당시)", "분야(당시)"),
            score_row("지금 지시문으로 다시 채점", labels, "새 점수", "새 분야"),
            "",
        ]
        group_lines += grouping_report(name, labels, model)
        per_article.append(labels.assign(파일=name)[["파일", "기사링크", "제목", "노출", "사건", "중요도(당시)", "새 점수", "새 분야"]])

    lines += group_lines
    lines += ["", f"- Gemini 호출: {main.GEMINI_USAGE['calls']}회 (429 {main.GEMINI_USAGE['rate_limited']}회, "
              f"기타 오류 {main.GEMINI_USAGE['errors']}회)"]
    text = "\n".join(lines)
    print(text)

    os.makedirs(RESULTS_DIR, exist_ok=True)
    with open(os.path.join(RESULTS_DIR, "latest.md"), "w", encoding="utf-8") as f:
        f.write(text + "\n")
    pd.concat(per_article).to_csv(os.path.join(RESULTS_DIR, "latest.csv"), index=False, encoding="utf-8-sig")
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write(text + "\n")


if __name__ == "__main__":
    main_eval()
