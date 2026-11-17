"""Gemini 호출 공용 도우미 - collect_news.py(뉴스)·collect_bills.py(법안)·collect_decisions.py(심결)가 같이 쓴다.

예전엔 세 파일이 "503이면 5초 뒤 한 번 더" 함수를 각자 복사해 갖고 있어서, 11/05~06 Gemini 과부하
(503 high demand) 때 collect_news.py만 고치고 법안 쪽은 그대로 실패했다(11/05 발의 법안 39건 중 20건 요약 없음).
이제 재시도·대체 모델 로직은 여기 한 곳에만 둔다. 각 스크립트는 따로 실행되는 프로세스라
호출 수·대기 시간 기록(GEMINI_USAGE, GEMINI_OVERLOAD)도 스크립트마다 따로 센다.
"""
import json
import os
import re
import time

import requests

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip().replace('"', '').replace("'", "")

# 이번 실행의 Gemini 호출 수. 뉴스·법안·심결·의원뉴스가 같은 무료 키를 나눠 쓰므로,
# 실제 사용량을 gemini_usage.csv에 남겨 한도에 얼마나 가까운지 본다(save_gemini_usage).
GEMINI_USAGE = {"calls": 0, "rate_limited": 0, "errors": 0}
# 오류가 어떤 것이었는지 gemini_usage.csv "오류 예시" 칸에 남긴다. Actions 로그는 회사 PC에서
# 로그인이 안 돼 못 보므로, 11/05 12:01부터 호출 대부분이 실패했을 때 원인을 알 수 없었다.
GEMINI_ERROR_SAMPLES = {}  # {"503 ...본문 앞부분": 횟수}


def note_gemini_error(text):
    text = " ".join(str(text).split())[:160]
    if text in GEMINI_ERROR_SAMPLES or len(GEMINI_ERROR_SAMPLES) < 5:
        GEMINI_ERROR_SAMPLES[text] = GEMINI_ERROR_SAMPLES.get(text, 0) + 1


# 503 "This model is currently experiencing high demand"(11/05 12:01~11/06 06:01, 실행마다 호출의
# 80% 이상 실패) 대응. 그 전에는 5초 뒤 한 번만 다시 시도해서 거의 다 실패로 끝났고, 실패한 기사는
# 다음 실행에 다시 분석 대상이 되어(load_analyzed_links) 실패가 눈덩이처럼 불었다(157→337→1941건).
# 이제는 (1) 조금 더 기다렸다 다시 시도하고, (2) 그래도 503이면 같은 키로 쓸 수 있는 다른 Gemini 3
# 모델로 보낸다. 오류 문구가 "이 모델"의 수요 문제라 다른 모델은 대개 받아 준다.
GEMINI_PRIMARY_MODEL = "gemini-3.5-flash-lite"
# 대체 후보 - 앞에 있을수록 우선. thinkingConfig.thinkingLevel을 쓰는 Gemini 3 계열만 둔다
# (2.5 계열은 이 설정을 400으로 거절한다). 실제로 있는지는 실행할 때 모델 목록으로 확인한다.
GEMINI_FALLBACK_PREFERENCE = ["gemini-3-flash-lite", "gemini-3.5-flash", "gemini-3-flash"]
# 같은 모델로 다시 시도하기 전 대기(초). 무료 등급은 503으로 실패한 요청도 하루 한도(500회)에
# 들어가서(AI Studio 사용량 화면으로 확인, 11/06) 재시도를 많이 하면 장애 때 한도가 더 빨리 준다.
# 그래서 같은 모델 재시도는 1번만 하고 바로 대체 모델로 넘긴다.
GEMINI_503_WAITS = [15]
GEMINI_503_WAIT_BUDGET = 600         # 한 실행에서 503 때문에 기다리는 시간 합계 상한(초)
GEMINI_FALLBACK_STICKY_CALLS = 20    # 대체 모델로 넘어가면 이만큼은 계속 쓰고, 그 뒤 원래 모델을 다시 시도
GEMINI_OVERLOAD = {"waited": 0, "sticky_left": 0, "fallbacks": None, "bad": set(), "fallback_ok": 0,
                   "daily_exhausted": set()}   # 하루 한도를 다 쓴 모델 - 이번 실행에선 더 보내지 않는다


# ── Cloudflare Workers AI: Gemini가 모두 실패했을 때 마지막으로 보내는 곳 (2026-11-18) ──
# 11/10~11/18 26회 실행에서 호출 1,925회 중 약 30%가 실패했다(대부분 503 high demand). 대체 모델
# gemini-3.5-flash는 하루 20회 한도라 금방 바닥나서, 그 뒤로는 "대체 모델 없음"으로 끝났다.
# 그래서 원래 모델 → Gemini 대체 모델 → Workers AI 순서로 보낸다. 전부 Workers AI로 옮기지 않는
# 까닭: 뉴스 채점 지시문을 Gemini 기준으로 eval로 맞춰 왔고(eval/), Llama의 한국어 판단 품질은
# 아직 재 보지 않았다. 실패해서 '분석 대기'로 남는 것보다는 낫다는 판단으로 실패분만 넘긴다.
# Worker 없이 Actions의 Python에서 REST API로 바로 부른다. worker/와 같은 모델이다
# (worker/src/index.js 주석: 8B급은 JSON을 자주 깨뜨려 70B로 올림).
# 토큰에 "Workers AI" 권한이 없으면 401/403이 나고, 그 실행에서는 다시 보내지 않는다.
CF_ACCOUNT_ID = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
CF_AI_TOKEN = (os.environ.get("CLOUDFLARE_AI_TOKEN") or os.environ.get("CLOUDFLARE_API_TOKEN") or "").strip()
WORKERS_AI_MODEL = os.environ.get("WORKERS_AI_MODEL", "").strip() or "@cf/meta/llama-3.3-70b-instruct-fp8-fast"
# 한 실행에서 Workers AI로 보내는 최대 횟수. 무료 몫(하루 10,000 뉴런)으로는 70B 묶음 분석이
# 하루 20회 안팎밖에 안 되고(추정), 유료 플랜이면 그만큼 요금이 붙으므로 상한을 둔다.
WORKERS_AI_MAX_CALLS = int(os.environ.get("WORKERS_AI_MAX_CALLS", "") or 40)
WORKERS_AI_MAX_TOKENS = 4096   # Workers AI 기본값(256)이면 20건 묶음 응답이 중간에 잘린다
WORKERS_AI = {"calls": 0, "ok": 0, "fail": 0, "off": ""}   # off: 이번 실행에서 끈 까닭


def workers_ai_available():
    if not (CF_ACCOUNT_ID and CF_AI_TOKEN):
        return False
    return not WORKERS_AI["off"] and WORKERS_AI["calls"] < WORKERS_AI_MAX_CALLS


def workers_ai_summary():
    """gemini_usage.csv·로그에 붙일 한 줄. 이번 실행에서 한 번도 안 썼으면 빈 문자열."""
    if not WORKERS_AI["calls"] and not WORKERS_AI["off"]:
        return ""
    s = f"Workers AI 성공 {WORKERS_AI['ok']}/{WORKERS_AI['calls']}회"
    return s + (f"({WORKERS_AI['off']})" if WORKERS_AI["off"] else "")


def _clean_json_text(raw):
    """Llama는 JSON 앞뒤에 설명이나 ``` 를 붙이기도 해서, 읽히는 JSON만 남겨 돌려준다.
    호출하는 쪽은 Gemini 응답처럼 바로 json.loads 하므로 여기서 정리해 둔다."""
    if isinstance(raw, (dict, list)):
        return json.dumps(raw, ensure_ascii=False)
    text = str(raw or "").strip()
    try:
        json.loads(text)
        return text
    except ValueError:
        pass
    starts = [i for i in (text.find("{"), text.find("[")) if i != -1]
    if starts:
        start = min(starts)
        end = text.rfind("}" if text[start] == "{" else "]")
        if end > start:
            try:
                return json.dumps(json.loads(text[start:end + 1]), ensure_ascii=False)
            except ValueError:
                pass
    return text   # 그래도 안 읽히면 원문 그대로 - 호출하는 쪽이 파싱 실패로 처리한다


class _WorkersAIResponse:
    """Workers AI 결과를 Gemini generateContent 응답 모양으로 감싼다(호출하는 쪽 코드를 안 바꾸려고)."""
    status_code = 200

    def __init__(self, text, usage):
        self._body = {
            "candidates": [{"content": {"parts": [{"text": text}]}}],
            "usageMetadata": {"promptTokenCount": usage.get("prompt_tokens", 0),
                              "candidatesTokenCount": usage.get("completion_tokens", 0),
                              "totalTokenCount": usage.get("total_tokens", 0)},
            "modelVersion": WORKERS_AI_MODEL,
        }
        self.text = json.dumps(self._body, ensure_ascii=False)

    def json(self):
        return self._body


def _payload_prompt(payload):
    """Gemini payload의 글을 하나로 모은다. 이 프로젝트의 호출은 모두 contents[0].parts[0].text 하나다."""
    parts = []
    sys_inst = payload.get("systemInstruction") or payload.get("system_instruction") or {}
    parts += [p.get("text", "") for p in sys_inst.get("parts", [])]
    for c in payload.get("contents", []):
        parts += [p.get("text", "") for p in c.get("parts", [])]
    return "\n\n".join(p for p in parts if p)


def _post_workers_ai(payload, timeout=120):
    """Gemini가 모두 실패한 generateContent 호출을 Workers AI로 한 번 보낸다. 성공하면 응답, 아니면 None."""
    if not workers_ai_available():
        return None
    prompt = _payload_prompt(payload)
    if not prompt:
        return None
    gen = payload.get("generationConfig", {})
    wants_json = "json" in str(gen.get("response_mime_type") or gen.get("responseMimeType") or "")
    system = ("당신은 한국어 글을 정확하게 읽고 지시대로 답하는 어시스턴트입니다."
              + (" 설명이나 ``` 없이 지시받은 JSON 하나만 출력하세요." if wants_json else ""))
    body = {"messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            "max_tokens": WORKERS_AI_MAX_TOKENS, "temperature": 0.2}
    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/run/{WORKERS_AI_MODEL}"
    WORKERS_AI["calls"] += 1
    try:
        res = requests.post(url, json=body, headers={"Authorization": f"Bearer {CF_AI_TOKEN}"}, timeout=timeout)
    except requests.exceptions.RequestException as e:
        WORKERS_AI["fail"] += 1
        note_gemini_error(f"Workers AI {type(e).__name__} {e}")
        return None
    if res.status_code != 200:
        WORKERS_AI["fail"] += 1
        note_gemini_error(f"Workers AI {res.status_code} {res.text[:140]}")
        if res.status_code in (400, 401, 403, 404):
            # 토큰 권한·계정 ID·모델 이름 문제 - 이번 실행에서 다시 보내도 같다.
            WORKERS_AI["off"] = f"{res.status_code} 응답으로 끔" + (" - 토큰에 Workers AI 권한 필요"
                                                           if res.status_code in (401, 403) else "")
            print(f"[Workers AI 끔] {WORKERS_AI['off']}: {res.text[:200]}")
        elif res.status_code == 429:
            WORKERS_AI["off"] = "429 한도로 끔"
        return None
    try:
        result = res.json().get("result") or {}
        text = result.get("response")
        if text is None or text == "":
            raise ValueError("빈 응답")
        out = _WorkersAIResponse(_clean_json_text(text) if wants_json else str(text), result.get("usage") or {})
    except ValueError as e:
        WORKERS_AI["fail"] += 1
        note_gemini_error(f"Workers AI 응답 처리 {e}")
        return None
    WORKERS_AI["ok"] += 1
    return out


def _last_resort(url, payload, res):
    """Gemini 쪽이 모두 실패했을 때: generateContent면 Workers AI를 시도하고, 안 되면 원래 실패 응답."""
    if ":generateContent" in url:
        wa = _post_workers_ai(payload)
        if wa is not None:
            return wa
    return res


class _QuotaExhaustedResponse:
    """하루 한도를 다 쓴 뒤엔 실제로 보내지 않고 이 가짜 429를 돌려준다(호출하는 쪽은 그대로 실패 처리)."""
    status_code = 429
    text = '{"error": {"code": 429, "message": "하루 무료 한도 소진 - 이번 실행에서는 Gemini 호출을 건너뜀"}}'

    def json(self):
        return json.loads(self.text)


def gemini_daily_exhausted():
    """원래 모델과 쓸 수 있는 대체 모델이 모두 하루 한도를 다 썼으면 True.
    collect_news.py 뉴스 분석은 이걸 보고 남은 묶음을 보내지 않고 '분석 대기'로 남긴다.
    Workers AI를 아직 쓸 수 있으면 False - 남은 묶음은 Workers AI가 받는다(횟수 상한까지)."""
    if GEMINI_PRIMARY_MODEL not in GEMINI_OVERLOAD["daily_exhausted"]:
        return False
    return not _gemini_fallback_models() and not workers_ai_available()


def _gemini_fallback_models():
    """이 키로 generateContent를 쓸 수 있는 대체 모델 이름(우선순위 순). 한 번만 조회한다."""
    if GEMINI_OVERLOAD["fallbacks"] is None:
        names = set()
        try:
            res = requests.get("https://generativelanguage.googleapis.com/v1beta/models?pageSize=200",
                               headers={"x-goog-api-key": GEMINI_API_KEY}, timeout=30)
            if res.status_code == 200:
                names = {m["name"].split("/", 1)[-1] for m in res.json().get("models", [])
                         if "generateContent" in m.get("supportedGenerationMethods", [])}
            else:
                print(f"[대체 모델 조회 실패] HTTP {res.status_code}")
        except Exception as e:
            print(f"[대체 모델 조회 예외] {e}")
        GEMINI_OVERLOAD["fallbacks"] = [m for m in GEMINI_FALLBACK_PREFERENCE if m in names]
        print(f"[대체 모델 후보] {GEMINI_OVERLOAD['fallbacks'] or '없음'}")
    return [m for m in GEMINI_OVERLOAD["fallbacks"]
            if m not in GEMINI_OVERLOAD["bad"] and m not in GEMINI_OVERLOAD["daily_exhausted"]]


_QUOTA_SEEN = set()


def _is_daily_quota(text):
    """429가 하루 한도인지 판단한다. 응답의 QuotaFailure.quotaId에 "PerDay"가 있거나
    (예: GenerateRequestsPerDayPerProjectPerModel-FreeTier), 다시 시도하라는 대기 시간이 10분을 넘으면
    하루 한도로 본다. 분당 한도(15회/분)는 잠깐 기다리면 풀리므로 멈추면 안 된다."""
    if re.search(r'"quotaId"\s*:\s*"[^"]*PerDay', text):
        return True
    m = re.search(r'"retryDelay"\s*:\s*"(\d+)', text) or re.search(r"retry in (\d+(?:\.\d+)?)s", text)
    return bool(m) and float(m.group(1)) > 600


def _note_quota(res, model):
    """429 응답 본문에서 어떤 한도(분당/하루, 어느 모델)에 걸렸는지 뽑아 기록한다.
    로그 출력은 본문 앞 300자에서 잘려 "* Quota ex..."까지만 보였다(11/06 08시 실행).
    하루 한도면 그 모델을 이번 실행에서 더 쓰지 않는다(11/06 10시 실행: 159회 중 156회가 429)."""
    text = res.text or ""
    daily = _is_daily_quota(text)
    if daily:
        GEMINI_OVERLOAD["daily_exhausted"].add(model)
    found = re.findall(r"Quota exceeded for metric: ([^,\s]+), limit: (\d+)(?:, model: ([\w.\-]+))?", text)
    key = "; ".join(f"{m.rsplit('/', 1)[-1]} 한도 {l}{' (' + mo + ')' if mo else ''}" for m, l, mo in found) or text[:140]
    key = ("하루 " if daily else "") + key
    note_gemini_error(f"429 {key}")
    if key not in _QUOTA_SEEN:
        _QUOTA_SEEN.add(key)
        print(f"[Gemini 한도 초과] {key}")


def _model_of(url):
    m = re.search(r"/models/([^:/?]+)", url)
    return m.group(1) if m else ""


def _post_once(url, payload, timeout):
    model = _model_of(url)
    if model in GEMINI_OVERLOAD["daily_exhausted"]:
        return _QuotaExhaustedResponse()
    GEMINI_USAGE["calls"] += 1
    res = requests.post(url, json=payload, headers={"x-goog-api-key": GEMINI_API_KEY}, timeout=timeout)
    if res.status_code == 429:
        GEMINI_USAGE["rate_limited"] += 1
        _note_quota(res, model)
    elif res.status_code != 200:
        GEMINI_USAGE["errors"] += 1
        note_gemini_error(f"{res.status_code} {res.text[:140]}")
    return res


def _try_fallbacks(url, payload, timeout, start_sticky=True):
    """원래 모델이 503·429일 때 대체 모델로 보낸다. 성공하면 그 응답, 모두 안 되면 None.
    start_sticky=False(이미 대체 모델을 쓰는 중)면 남은 횟수를 늘리지 않아, 정해진 횟수 뒤에는
    원래 모델을 다시 시도하게 된다."""
    for model in _gemini_fallback_models():
        res = _post_once(url.replace(GEMINI_PRIMARY_MODEL, model), payload, timeout)
        if res.status_code == 200:
            if start_sticky:
                GEMINI_OVERLOAD["sticky_left"] = GEMINI_FALLBACK_STICKY_CALLS
            GEMINI_OVERLOAD["fallback_ok"] += 1
            # 정렬해서 다음 호출도 방금 된 모델부터 쓰게 한다.
            GEMINI_OVERLOAD["fallbacks"].remove(model)
            GEMINI_OVERLOAD["fallbacks"].insert(0, model)
            return res
        if res.status_code in (400, 403, 404):
            GEMINI_OVERLOAD["bad"].add(model)   # 이 키·설정으로는 못 쓰는 모델 - 이번 실행에선 다시 안 씀
    return None


def post_gemini_with_retry(url, payload, timeout=30, retries=1, retry_wait=5):
    """Gemini 호출 하나를 감싸서, 서버 과부하(503)나 타임아웃처럼 '잠깐 있다 다시 하면
    될' 일시적 오류일 때만 다시 시도한다. 원래 모델(GEMINI_PRIMARY_MODEL)이 503이면
    대기 후 재시도, 그래도 안 되면 대체 모델로, 그것도 안 되면 Workers AI로 보낸다(위 설명). 그 외(200 성공, 4xx 등
    영구적 오류)는 그대로 반환해서 기존 처리 로직이 그대로 판단하게 둔다."""
    uses_primary = GEMINI_PRIMARY_MODEL in url
    # 직전에 원래 모델이 과부하였으면 한동안은 바로 대체 모델로 보낸다(503 대기를 매번 반복하지 않게).
    if uses_primary and GEMINI_OVERLOAD["sticky_left"] > 0:
        GEMINI_OVERLOAD["sticky_left"] -= 1
        try:
            res = _try_fallbacks(url, payload, timeout, start_sticky=False)
            if res is not None:
                return res
        except requests.exceptions.RequestException as e:
            GEMINI_USAGE["errors"] += 1
            note_gemini_error(f"{type(e).__name__} {e}")
    last_exc = None
    waits = GEMINI_503_WAITS if uses_primary else [retry_wait] * retries
    for attempt in range(len(waits) + 1):
        try:
            res = _post_once(url, payload, timeout)
            # 429(한도 초과)도 대체 모델로 보낸다. 무료 등급 한도는 모델마다 따로라, 11/06 08시
            # 실행에서 원래 모델이 429로 막힌 뒤에도(53회) 대체 모델은 받아 줄 여지가 있었다.
            # 하루 한도면 기다려도 소용없으므로 대기 없이 바로 넘긴다.
            if res.status_code == 429 and uses_primary:
                fb = _try_fallbacks(url, payload, timeout)
                return fb if fb is not None else _last_resort(url, payload, res)
            if res.status_code != 503:
                return res
            wait = waits[attempt] if attempt < len(waits) else None
            if uses_primary and (wait is None or GEMINI_OVERLOAD["waited"] + wait > GEMINI_503_WAIT_BUDGET):
                fb = _try_fallbacks(url, payload, timeout)
                return fb if fb is not None else _last_resort(url, payload, res)
            if wait is None:
                return res
            GEMINI_OVERLOAD["waited"] += wait if uses_primary else 0
            time.sleep(wait)
        except requests.exceptions.RequestException as e:
            GEMINI_USAGE["errors"] += 1
            note_gemini_error(f"{type(e).__name__} {e}")
            last_exc = e
            if attempt < retries:
                time.sleep(retry_wait)
                continue
            # 타임아웃 등 통신 오류가 끝까지 나면 Workers AI에 한 번 맡겨 본다(11/17 06시: ReadTimeout 5회).
            if uses_primary and ":generateContent" in url:
                wa = _post_workers_ai(payload)
                if wa is not None:
                    return wa
            raise
    raise last_exc
