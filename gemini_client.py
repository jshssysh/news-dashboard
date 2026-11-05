"""Gemini 호출 공용 도우미 - main.py(뉴스)·collect_bills.py(법안)·collect_decisions.py(심결)가 같이 쓴다.

예전엔 세 파일이 "503이면 5초 뒤 한 번 더" 함수를 각자 복사해 갖고 있어서, 11/05~06 Gemini 과부하
(503 high demand) 때 main.py만 고치고 법안 쪽은 그대로 실패했다(11/05 발의 법안 39건 중 20건 요약 없음).
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


class _QuotaExhaustedResponse:
    """하루 한도를 다 쓴 뒤엔 실제로 보내지 않고 이 가짜 429를 돌려준다(호출하는 쪽은 그대로 실패 처리)."""
    status_code = 429
    text = '{"error": {"code": 429, "message": "하루 무료 한도 소진 - 이번 실행에서는 Gemini 호출을 건너뜀"}}'

    def json(self):
        return json.loads(self.text)


def gemini_daily_exhausted():
    """원래 모델과 쓸 수 있는 대체 모델이 모두 하루 한도를 다 썼으면 True.
    main.py 뉴스 분석은 이걸 보고 남은 묶음을 보내지 않고 '분석 대기'로 남긴다."""
    if GEMINI_PRIMARY_MODEL not in GEMINI_OVERLOAD["daily_exhausted"]:
        return False
    return not _gemini_fallback_models()


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
    대기 후 재시도, 그래도 안 되면 대체 모델로 보낸다(위 설명). 그 외(200 성공, 4xx 등
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
                return fb if fb is not None else res
            if res.status_code != 503:
                return res
            wait = waits[attempt] if attempt < len(waits) else None
            if uses_primary and (wait is None or GEMINI_OVERLOAD["waited"] + wait > GEMINI_503_WAIT_BUDGET):
                fb = _try_fallbacks(url, payload, timeout)
                return fb if fb is not None else res
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
            raise
    raise last_exc
