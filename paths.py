"""저장소 안 파일 경로를 한 곳에 모은 파일.

수집기·main.py·generate_html.py·eval 스크립트는 파일 이름을 직접 적지 않고 여기 상수를 쓴다.
파일을 옮길 때(예: 데이터 → data/<분야>/)는 이 파일만 고치면 된다.
단, .github/workflows/daily.yml의 `git add` 목록은 셸이라 여기를 못 쓰므로 함께 고쳐야 한다.

경로는 모두 저장소 루트(ROOT) 기준 절대경로다. 어느 폴더에서 실행해도 같은 파일을 가리킨다.
"""
import os

ROOT = os.path.dirname(os.path.abspath(__file__))


def _p(*parts):
    return os.path.join(ROOT, *parts)


# ── 설정(사람이 고치는 파일) ─────────────────────────────
CONFIG_DIR = _p("config")
KEYWORDS_CONFIG = _p("config", "keywords.yaml")
BILL_KEYWORDS = _p("config", "bill_keywords.yaml")
BILL_VETOED = _p("config", "bill_vetoed.csv")
COMPANY_ALIASES = _p("config", "company_aliases.csv")
NEWS_OVERRIDES = _p("config", "news_overrides.csv")
PERSONNEL = _p("config", "personnel.csv")

# ── 뉴스 (main.py) ───────────────────────────────────────
NEWS_LIST = _p("news_list.csv")
NEWS_DESCRIPTIONS = _p("news_descriptions.csv")
REJECTED_NEWS = _p("rejected_news.csv")
CLUSTER_WARNINGS = _p("cluster_warnings.csv")
EMBED_ATTACH_LOG = _p("embed_attach_log.csv")
ISSUE_DISPLAY_NAMES = _p("issue_display_names.csv")
EMBED_CACHE = _p(".cache", "embeddings.json.gz")  # 커밋 안 함, Actions 캐시

# ── 법안·의원 (collect_bills.py, collect_member_news.py) ──
BILL_LIST = _p("bill_list.csv")
BILL_STATUS_LOG = _p("bill_status_log.csv")
MEMBER_LIST = _p("member_list.csv")
MEMBER_NEWS = _p("member_news.csv")

# ── 심·판결 (collect_decisions.py) ───────────────────────
DECISION_LIST = _p("decision_list.csv")

# ── 법령 (collect_law_*.py, collect_subordinate_laws.py, collect_legislative_notices.py) ──
LAW_PENALTY_LIST = _p("law_penalty_list.csv")
CONTRACT_LAW_CORPUS = _p("contract_law_corpus.csv")
SUBORDINATE_LAW_LIST = _p("subordinate_law_list.csv")
SUBORDINATE_LAW_DIFFS = _p("subordinate_law_diffs.json")
LAW_TEXTS = _p("law_texts.json")
LEGISLATIVE_NOTICE_LIST = _p("legislative_notice_list.csv")
NOTICE_LIST = _p("notice_list.csv")  # collect_notices.py(정기 실행에 없음)

# ── 공통 ─────────────────────────────────────────────────
GEMINI_USAGE = _p("gemini_usage.csv")

# ── 화면 (generate_html.py → docs/, GitHub Pages가 그대로 보여 줌) ──
# docs/ 안 파일 이름은 화면(html_template.html)과 Worker가 주소로 직접 받으므로 바꾸지 않는다.
HTML_TEMPLATE = _p("html_template.html")
DOCS_DIR = _p("docs")
NEWS_ARCHIVE_DIR = _p("docs", "archive")
