"""이슈 묶기 방식 비교: 정답 쌍(eval/merge_pairs_*.csv)으로 "두 이슈가 같은 사건인가"를 잰다.

GitHub Actions "뉴스 필터 평가 - 묶기" 워크플로가 실행한다. 쌍 파일에는 이슈명과 정답만
있고(30KB 업로드 제한), 각 이슈의 기사 제목은 Actions에 있는 news_list.csv에서 채운다.
결과는 eval/results/merge_latest.md로 저장한다.

비교하는 방식
  A. 지금 이슈명 규칙: 같은 당사자(이름 첫 단어)끼리만 이름 단어 비교(main._should_merge_issue)
  B. 임베딩: 이슈 기사 제목들을 임베딩해 코사인 유사도가 기준 이상이면 같은 사건
  C. 칸 나누기: Gemini가 이슈마다 당사자·대상·사건유형을 뽑고, 당사자와 대상이 함께 겹치거나
     대상이 강하게 겹치면 같은 사건(사건유형만 겹치면 다른 사건)
  D. 참고: Gemini에 두 이슈가 같은 사건인지 직접 물어봄
"""
import glob
import json
import os
import re
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import main  # noqa: E402

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent"
TITLES_PER_ISSUE = 5
EMBED_THRESHOLDS = [0.75, 0.80, 0.85, 0.90]


def ask_gemini(prompt):
    payload = {"contents": [{"parts": [{"text": prompt}]}],
               "generationConfig": {"response_mime_type": "application/json", "thinkingConfig": {"thinkingLevel": "low"}}}
    for wait in [0, 30, 60]:
        time.sleep(wait)
        res = main.post_gemini_with_retry(GEMINI_URL, payload)
        if res.status_code == 200:
            raw = res.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            raw = re.sub(r"^```(json)?|```$", "", raw).strip()
            try:
                return json.loads(raw)
            except Exception:
                return None
        if res.status_code != 429:
            print(f"[Gemini 실패] HTTP {res.status_code} {res.text[:200]}")
            return None
    return None


def issue_titles(news, issue, day):
    g = news[news["대표이슈"] == issue]
    same_day = g[g["수집일자"].astype(str).str[:10] == day]
    g = same_day if len(same_day) else g
    return g["제목"].drop_duplicates().head(TITLES_PER_ISSUE).tolist()


def norm(s):
    return re.sub(r"[^가-힣A-Za-z0-9]", "", str(s)).lower()


def fuzzy_overlap(xs, ys):
    """두 목록에 같은(한쪽이 다른 쪽을 포함하는) 항목이 하나라도 있으면 True."""
    xs = [norm(x) for x in xs if len(norm(x)) >= 2]
    ys = [norm(y) for y in ys if len(norm(y)) >= 2]
    return any(x in y or y in x for x in xs for y in ys)


def extract_fields(issues, titles):
    """이슈별 {"당사자": [...], "대상": [...], "사건유형": "..."}."""
    fields = {}
    keys = list(issues)
    for start in range(0, len(keys), 15):
        chunk = keys[start:start + 15]
        data = [{"id": i, "titles": titles[k]} for i, k in enumerate(chunk)]
        prompt = f"""다음은 뉴스 이슈별 기사 제목 묶음입니다: {json.dumps(data, ensure_ascii=False)}
각 이슈가 다루는 사건을 아래 세 칸으로 나눠 JSON 배열로 답하세요.
- id: 그대로
- 당사자: 사건의 주체·대상이 되는 구체적 기업·기관·인물명 목록(정식 명칭, 최대 5개).
  "공정위"·"정부"처럼 조사·발표만 하는 기관도 사건의 핵심이면 포함.
- 대상: 무엇에 관한 사건인지 핵심 고유 명사·사안 목록(최대 3개). 예: "솔리다임 미국 상장",
  "초광역특구법", "하도급 대금", "사료용 밀가루 부산물". "담합"·"제재"·"지배구조"처럼 사건
  종류만 나타내는 일반 단어는 넣지 말 것.
- 사건유형: 담합/제재/조사/소송/입법/인수합병/지분이동/인사/국감/실적/행사/기타 중 하나.
응답 예: [{{"id": 0, "당사자": ["서울중수청"], "대상": ["하도급 대금 미지급"], "사건유형": "조사"}}]"""
        out = ask_gemini(prompt) or []
        for r in out:
            try:
                fields[chunk[int(r["id"])]] = r
            except Exception:
                pass
        time.sleep(4.5)
    return fields


def structured_same(fa, fb):
    if not fa or not fb:
        return False
    actor = fuzzy_overlap(fa.get("당사자", []), fb.get("당사자", []))
    obj = fuzzy_overlap(fa.get("대상", []), fb.get("대상", []))
    return obj and (actor or fa.get("사건유형") == fb.get("사건유형"))


def judge_pairs(pairs, titles):
    verdict = {}
    rows = pairs.to_dict("records")
    for start in range(0, len(rows), 20):
        chunk = rows[start:start + 20]
        data = [{"id": i, "A": titles[(r["이슈A"], r["날짜"])], "B": titles[(r["이슈B"], r["날짜"])]}
                for i, r in enumerate(chunk)]
        prompt = f"""다음은 뉴스 이슈 쌍(A, B)별 기사 제목입니다: {json.dumps(data, ensure_ascii=False)}
각 쌍이 같은 사건(같은 당사자의 같은 일, 또는 같은 사안의 후속 보도)을 다루면 true, 아니면 false로
JSON 배열로 답하세요. 같은 업종·같은 종류의 일이라도 당사자나 사안이 다르면 false.
응답 예: [{{"id": 0, "same": true}}]"""
        out = ask_gemini(prompt) or []
        for r in out:
            try:
                verdict[chunk[int(r["id"])]["쌍"]] = bool(r["same"])
            except Exception:
                pass
        time.sleep(4.5)
    return verdict


def row(name, pred, gold):
    y = gold == "Y"
    tp = int((pred & y).sum()); fp = int((pred & ~y).sum())
    return (f"| {name} | {tp}/{int(y.sum())} ({tp / max(int(y.sum()), 1):.0%}) | "
            f"{fp}/{int((~y).sum())} ({fp / max(int((~y).sum()), 1):.0%}) |")


def main_eval():
    if not main.GEMINI_API_KEY:
        sys.exit("GEMINI_API_KEY가 없습니다 - GitHub Actions에서 실행하세요.")
    path = sorted(glob.glob(os.path.join(HERE, "merge_pairs_*.csv")))[-1]
    pairs = pd.read_csv(path, encoding="utf-8-sig")
    news = pd.read_csv(os.path.join(HERE, "..", "news_list.csv"), encoding="utf-8-sig",
                       usecols=["수집일자", "대표이슈", "제목"])
    keys = {(r["이슈A"], r["날짜"]) for _, r in pairs.iterrows()} | {(r["이슈B"], r["날짜"]) for _, r in pairs.iterrows()}
    titles = {k: issue_titles(news, *k) for k in keys}
    missing = sum(1 for v in titles.values() if not v)
    gold = pairs["같은 사건"]

    # A. 지금 이슈명 규칙
    def name_rule(a, b):
        ta, tb = main._issue_tokens(a), main._issue_tokens(b)
        if not ta or not tb or main._canonical_root(ta[0]) != main._canonical_root(tb[0]):
            return False
        return main._should_merge_issue(ta, tb)
    pred_a = pairs.apply(lambda r: name_rule(r["이슈A"], r["이슈B"]), axis=1)

    # B. 임베딩
    model = main.pick_embedding_model()
    klist = sorted(keys)
    vec = {}
    if model:
        raw = main.embed_texts([" / ".join(titles[k]) for k in klist], model)
        for k, v in zip(klist, raw):
            if v is not None:
                v = np.asarray(v, dtype=float)
                vec[k] = v / np.linalg.norm(v)
    sims = pairs.apply(lambda r: float(np.dot(vec[(r["이슈A"], r["날짜"])], vec[(r["이슈B"], r["날짜"])]))
                       if (r["이슈A"], r["날짜"]) in vec and (r["이슈B"], r["날짜"]) in vec else np.nan, axis=1)

    # C. 칸 나누기
    fields = extract_fields(klist, titles)
    pred_c = pairs.apply(lambda r: structured_same(fields.get((r["이슈A"], r["날짜"])), fields.get((r["이슈B"], r["날짜"]))), axis=1)

    # D. 직접 판정
    verdict = judge_pairs(pairs, titles)
    pred_d = pairs["쌍"].map(lambda i: verdict.get(i, False))

    lines = [
        "## 이슈 묶기 방식 비교",
        "",
        f"- 실행: {main.datetime.now(main.KST).strftime('%Y-%m-%d %H:%M')} KST, 정답 {os.path.basename(path)} "
        f"({len(pairs)}쌍: 같은 사건 {int((gold == 'Y').sum())}, 다른 사건 {int((gold == 'N').sum())})",
        f"- 제목을 못 찾은 이슈 {missing}개, 임베딩 모델 {model or '없음'}, 칸 추출 {len(fields)}/{len(klist)}개, "
        f"직접 판정 {len(verdict)}/{len(pairs)}쌍",
        "",
        "| 방식 | 같은 사건을 묶음(높을수록 좋음) | 다른 사건을 묶음(낮을수록 좋음) |",
        "|---|---|---|",
        row("A. 지금 이슈명 규칙", pred_a, gold),
    ]
    for t in EMBED_THRESHOLDS:
        lines.append(row(f"B. 임베딩 {t:.2f} 이상", sims >= t, gold))
    lines += [
        row("C. 칸 나누기(당사자·대상)", pred_c, gold),
        row("C+B. 칸 나누기 또는 임베딩 0.85", pred_c | (sims >= 0.85), gold),
        row("D. Gemini 직접 판정", pred_d, gold),
        "",
        "### 칸 나누기가 틀린 쌍",
        "",
        "| 정답 | 임베딩 | A 당사자 / 대상 | B 당사자 / 대상 |",
        "|---|---|---|---|",
    ]
    for i, r in pairs[pred_c != (gold == "Y")].iterrows():
        fa = fields.get((r["이슈A"], r["날짜"])) or {}
        fb = fields.get((r["이슈B"], r["날짜"])) or {}
        lines.append(f"| {r['같은 사건']} | {sims[i]:.2f} | {', '.join(fa.get('당사자', []))} / {', '.join(fa.get('대상', []))} | "
                     f"{', '.join(fb.get('당사자', []))} / {', '.join(fb.get('대상', []))} |")
    lines += ["", f"- Gemini 호출: {main.GEMINI_USAGE['calls']}회 (429 {main.GEMINI_USAGE['rate_limited']}회)"]
    text = "\n".join(lines)
    print(text)
    os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
    with open(os.path.join(HERE, "results", "merge_latest.md"), "w", encoding="utf-8") as f:
        f.write(text + "\n")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(text + "\n")


if __name__ == "__main__":
    main_eval()
