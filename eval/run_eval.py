"""정답 데이터(eval/labels_*.csv)로 지금의 Gemini 채점 기준을 시험한다.

GitHub Actions의 "뉴스 필터 평가" 워크플로가 실행한다(회사 PC에는 Gemini 키가 없음).
데이터는 아무것도 저장/커밋하지 않고, 결과는 실행 로그와 실행 요약 화면에만 남긴다.

한계: 정답 데이터에는 기사 제목만 있고 네이버 설명이 없어서, 실제 수집 때(제목+설명)보다
판단 근거가 적다. 그래서 "당시 점수"(옛 기준, 제목+설명 80자)와의 비교는 참고용이다.
"""
import glob
import os
import sys
import time

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import main  # noqa: E402

DISPLAY_MIN_SCORE = 6  # generate_html.DISPLAY_MIN_ISSUE_SCORE와 같은 기준
HIDDEN = {"삼성그룹", "삼성물산", "공정위인사"}


def report(name, show, is_y):
    shown = int(show.sum())
    noise = int((show & ~is_y).sum())
    missed = int((~show & is_y).sum())
    return (f"| {name} | {shown} | {noise} ({noise / max(shown, 1):.0%}) | "
            f"{missed} ({missed / max(int(is_y.sum()), 1):.0%}) |")


def main_eval():
    if not main.GEMINI_API_KEY:
        sys.exit("GEMINI_API_KEY가 없습니다 - GitHub Actions에서 실행하세요.")
    path = sorted(glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)), "labels_*.csv")))[-1]
    labels = pd.read_csv(path, encoding="utf-8-sig")
    items = [
        {"idx": i, "title": r["제목"], "description": "", "category": r["분야(당시)"]}
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
        "| 기준 | 화면 기사 | 잡음(나오면 안 되는 기사) | 놓침(나와야 하는데 빠진 기사) |",
        "|---|---|---|---|",
        report("당시 점수, 5점 이상", old_view["중요도(당시)"] >= 5, old_view["노출"] == "Y"),
        report("당시 점수, 6점 이상", old_view["중요도(당시)"] >= 6, old_view["노출"] == "Y"),
        report(f"새 기준, {DISPLAY_MIN_SCORE}점 이상", new_view["새 점수"] >= DISPLAY_MIN_SCORE, new_view["노출"] == "Y"),
        "",
        "제목만으로 채점했으므로(설명 없음) 실제 수집 때보다 불리한 조건입니다.",
        "",
        "### 새 기준에서 틀린 기사",
        "",
        "| 정답 | 새 점수 | 당시 점수 | 제목 |",
        "|---|---|---|---|",
    ]
    wrong = new_view[(new_view["새 점수"] >= DISPLAY_MIN_SCORE) != (new_view["노출"] == "Y")]
    for _, r in wrong.iterrows():
        lines.append(f"| {r['노출']} | {r['새 점수']} | {r['중요도(당시)']} | {str(r['제목']).replace('|', '/')} |")
    text = "\n".join(lines)
    print(text)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write(text + "\n")


if __name__ == "__main__":
    main_eval()
