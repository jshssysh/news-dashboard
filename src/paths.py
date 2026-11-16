"""저장소 안 파일 경로를 한 곳에 모은 파일.

코드는 모두 src/에 있고, 수집기·generate_html.py·eval 스크립트는 파일 이름을 직접 적지 않고 여기 상수를 쓴다.
파일을 옮길 때는 이 파일만 고치면 된다. 수집 결과는 모두 data/ 아래에 두고,
.github/workflows/daily.yml은 data/와 docs/를 폴더째 커밋한다.

경로는 모두 저장소 루트(ROOT) 기준 절대경로다. 어느 폴더에서 실행해도 같은 파일을 가리킨다.

분야 이름(폴더 이름)은 다섯 개로 통일한다:
  news(뉴스) · bills(입법) · members(의원) · decisions(심·판결) · law(법령)
"""
import os

SRC_DIR = os.path.dirname(os.path.abspath(__file__))  # 코드 폴더(src/)
ROOT = os.path.dirname(SRC_DIR)  # 저장소 루트


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

# ── 수집 결과(수집 봇이 만들고 커밋) ─────────────────────
DATA_DIR = _p("data")

# 뉴스 (collect_news.py)
NEWS_LIST = _p("data", "news", "news_list.csv")
NEWS_DESCRIPTIONS = _p("data", "news", "news_descriptions.csv")
REJECTED_NEWS = _p("data", "news", "rejected_news.csv")
CLUSTER_WARNINGS = _p("data", "news", "cluster_warnings.csv")
EMBED_ATTACH_LOG = _p("data", "news", "embed_attach_log.csv")
ISSUE_MERGE_CANDIDATES = _p("data", "news", "issue_merge_candidates.csv")  # 이슈끼리 합칠 후보(중심 유사도) 기록
ISSUE_AUTO_MERGES = _p("data", "news", "issue_auto_merges.csv")  # 이슈 자동 합치기 기록(Gemini 판정 포함)
ISSUE_DISPLAY_NAMES = _p("data", "news", "issue_display_names.csv")
EMBED_CACHE = _p(".cache", "embeddings.json.gz")  # 커밋 안 함, Actions 캐시

# 입법 (collect_bills.py)
BILL_LIST = _p("data", "bills", "bill_list.csv")
BILL_STATUS_LOG = _p("data", "bills", "bill_status_log.csv")

# 의원 (collect_bills.py가 의원 목록을, collect_member_news.py가 의원별 뉴스를 만든다)
MEMBER_LIST = _p("data", "members", "member_list.csv")
MEMBER_NEWS = _p("data", "members", "member_news.csv")

# 심·판결 (collect_decisions.py)
DECISION_LIST = _p("data", "decisions", "decision_list.csv")

# 법령 (collect_law_*.py, collect_subordinate_laws.py, collect_legislative_notices.py)
LAW_PENALTY_LIST = _p("data", "law", "law_penalty_list.csv")
CONTRACT_LAW_CORPUS = _p("data", "law", "contract_law_corpus.csv")
SUBORDINATE_LAW_LIST = _p("data", "law", "subordinate_law_list.csv")
SUBORDINATE_LAW_DIFFS = _p("data", "law", "subordinate_law_diffs.json")
LAW_TEXTS = _p("data", "law", "law_texts.json")
LEGISLATIVE_NOTICE_LIST = _p("data", "law", "legislative_notice_list.csv")

# 공통
GEMINI_USAGE = _p("data", "gemini_usage.csv")

# ── 화면 (generate_html.py → docs/, GitHub Pages가 그대로 보여 줌) ──
# docs/ 위치와 그 안 파일 이름은 화면(html_template.html)과 Worker가 주소로 직접 받으므로 바꾸지 않는다.
HTML_TEMPLATE = _p("src", "html_template.html")
DOCS_DIR = _p("docs")
NEWS_ARCHIVE_DIR = _p("docs", "archive")
