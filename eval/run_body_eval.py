"""본문 앞부분 시험: 네이버 발췌문 대신 기사 본문 앞부분을 Gemini에 주면 요약·점수가 나아지는지 본다.

GitHub Actions "[평가] 뉴스 필터 - 본문" 워크플로가 실행한다. 수집에는 적용하지 않는다.
최근 화면 기사 표본마다
  1) 네이버 검색 발췌문(지금 방식)과 2) 기사 페이지 본문 앞 문단(최대 400자)을 각각 넣어
같은 지시문으로 요약·채점하고, 본문을 받아온 성공률과 걸린 시간을 함께 기록한다.
본문은 요약 비교에만 쓰고 저장하지 않는다(결과 파일에는 요약만 남김).
결과: eval/results/body_latest.md
"""
import os
import re
import sys
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import requests
from bs4 import BeautifulSoup

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import main  # noqa: E402

SAMPLE_SIZE = 60
BODY_CHARS = 400
BOILERPLATE = re.compile(r"Internet Explorer|무단전재|재배포|저작권|구독|로그인|기자 ?=|@[a-z0-9.]+\.(com|kr)|Copyright", re.I)
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}


def naver_description(title, link):
    query = re.sub(r"\[[^\]]*\]|\.{2,}|…$", " ", str(title)).strip()
    url = "https://openapi.naver.com/v1/search/news.json?display=30&sort=sim&query=" + urllib.parse.quote(query)
    headers = {"X-Naver-Client-Id": main.NAVER_CLIENT_ID, "X-Naver-Client-Secret": main.NAVER_CLIENT_SECRET}
    try:
        res = requests.get(url, headers=headers, timeout=10)
        for item in res.json().get("items", []) if res.status_code == 200 else []:
            if link in (item.get("originallink"), item.get("link")):
                return main.clean_text(item.get("description", ""))
    except requests.exceptions.RequestException:
        pass
    return ""


def body_lead(link):
    """(본문 앞부분, 걸린 초, 실패 사유). 실패하면 본문은 ""."""
    start = time.time()
    try:
        res = requests.get(link, headers=UA, timeout=8)
        if res.status_code != 200:
            return "", time.time() - start, f"HTTP {res.status_code}"
        res.encoding = res.apparent_encoding or res.encoding
        soup = BeautifulSoup(res.text, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form"]):
            tag.decompose()
        root = (soup.select_one("#dic_area") or soup.select_one("#articleBodyContents")
                or soup.select_one("article") or soup.select_one("[itemprop=articleBody]") or soup)
        parts, total = [], 0
        for p in root.find_all(["p", "div"], recursive=True):
            text = " ".join(p.get_text(" ", strip=True).split())
            if len(text) < 40 or BOILERPLATE.search(text) or text in parts:
                continue
            if p.find(["p", "div"]):  # 안쪽 문단을 따로 읽도록 바깥 덩어리는 건너뛴다
                continue
            parts.append(text)
            total += len(text)
            if total >= BODY_CHARS:
                break
        lead = " ".join(parts)[:BODY_CHARS]
        return lead, time.time() - start, "" if lead else "문단 없음"
    except Exception as e:
        return "", time.time() - start, type(e).__name__


def analyze(items):
    out = {}
    for s in range(0, len(items), 20):
        for idx, score, _t, summary, _sent, _cat in main.analyze_batch_with_gemini(items[s:s + 20]):
            out[idx] = (score, summary)
        time.sleep(4.5)
    return out


def main_eval():
    if not main.GEMINI_API_KEY:
        sys.exit("GEMINI_API_KEY가 없습니다 - GitHub Actions에서 실행하세요.")
    news = pd.read_csv(os.path.join(HERE, "..", "news_list.csv"), encoding="utf-8-sig")
    news["중요도"] = pd.to_numeric(news["중요도"], errors="coerce").fillna(0)
    recent = news[(news["수집일자"] >= news["수집일자"].max()[:10]) & (news["중요도"] >= 6)
                  & (news["논조"] != "판단 실패")].copy()
    recent["도메인"] = recent["기사링크"].str.extract(r"https?://([^/]+)")[0]
    sample = (recent.drop_duplicates("제목").sample(frac=1, random_state=1)
              .groupby("도메인").head(2).head(SAMPLE_SIZE).reset_index(drop=True))

    sample["발췌"] = [naver_description(r["제목"], r["기사링크"]) for _, r in sample.iterrows()]
    with ThreadPoolExecutor(max_workers=8) as pool:
        bodies = list(pool.map(body_lead, sample["기사링크"]))
    sample["본문"] = [b[0] for b in bodies]
    sample["본문 초"] = [b[1] for b in bodies]
    sample["본문 실패"] = [b[2] for b in bodies]

    base = [{"idx": i, "title": r["제목"], "description": r["발췌"][:main.ANALYZE_DESCRIPTION_CHARS], "category": r["분야"]}
            for i, r in sample.iterrows()]
    body = [{"idx": i, "title": r["제목"], "description": (r["본문"] or r["발췌"])[:BODY_CHARS], "category": r["분야"]}
            for i, r in sample.iterrows()]
    a, b = analyze(base), analyze(body)

    ok = sample["본문"] != ""
    diff = [b.get(i, (0, ""))[0] - a.get(i, (0, ""))[0] for i in sample.index]
    lines = [
        "## 본문 앞부분 시험",
        "",
        f"- 실행: {main.datetime.now(main.KST).strftime('%Y-%m-%d %H:%M')} KST, 표본 {len(sample)}건(언론사당 최대 2건)",
        f"- 본문 받아오기 성공 {int(ok.sum())}/{len(sample)}건, 평균 {sample['본문 초'].mean():.1f}초"
        f"(최대 {sample['본문 초'].max():.1f}초), 실패 사유: "
        + ", ".join(f"{k} {v}건" for k, v in sample.loc[~ok, "본문 실패"].value_counts().items()),
        f"- 점수 변화(본문 - 발췌): 같음 {sum(d == 0 for d in diff)}건, 오름 {sum(d > 0 for d in diff)}건, "
        f"내림 {sum(d < 0 for d in diff)}건",
        f"- Gemini 호출: {main.GEMINI_USAGE['calls']}회 (429 {main.GEMINI_USAGE['rate_limited']}회)",
        "",
        "| 제목 | 발췌로 만든 요약 (점수) | 본문으로 만든 요약 (점수) |",
        "|---|---|---|",
    ]
    for i, r in sample.iterrows():
        sa, sb = a.get(i, (0, "")), b.get(i, (0, ""))
        mark = "" if r["본문"] else " (본문 실패)"
        lines.append(f"| {str(r['제목']).replace('|', '/')} | {str(sa[1]).replace('|', '/')} ({sa[0]}) | "
                     f"{str(sb[1]).replace('|', '/')} ({sb[0]}){mark} |")
    text = "\n".join(lines)
    print(text)
    os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
    with open(os.path.join(HERE, "results", "body_latest.md"), "w", encoding="utf-8") as f:
        f.write(text + "\n")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(text + "\n")


if __name__ == "__main__":
    main_eval()
