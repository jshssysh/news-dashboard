/**
 * news-dashboard의 "법령" 탭에서 붙여넣은 계약서 문구를, 하도급·유통·가맹·
 * 대리점·약관 계열 법령의 조문 전체(contract_law_corpus.json)와 대조해
 * 저촉 소지가 있는 조문을 AI로 판단해준다.
 *
 * 이 프로젝트(news-dashboard)는 GitHub Pages 정적 사이트라 서버가 없어서,
 * "사용자가 그 순간 입력하는 임의의 텍스트"를 실시간으로 AI 검토하려면
 * 별도의 서버가 필요하다 - 이 Worker가 그 역할이다 (2026-09-14, 계약서
 * 조항 저촉 확인 기능 논의 결과).
 *
 * 처음엔 law_penalties.json(벌칙/과징금 조문만 좁힌 목록)을 후보로 썼는데,
 * "부당한 특약의 금지" 같은 금지·의무 조항 자체가 벌칙류 제목이 아니라서
 * 후보에 아예 없었다(실측: 명백한 부당특약 문구도 "저촉 조문 없음"으로
 * 오판). contract_law_corpus.json은 그 법령들의 조문 전체를 담고 있어
 * 이 문제가 없다 - 다만 693건이나 되므로, 매 요청마다 전부 프롬프트에
 * 넣지 않고 계약서 문구와 겹치는 상위 후보만 추려 보낸다 (selectTopCandidates
 * - 형태소 분석기 없이 글자 2-gram + 코퍼스 전체 IDF 가중치로 근사한 것이라
 * 완벽하진 않지만, 조사가 붙어 단어 형태가 달라져도 gram 단위로는 대부분
 * 겹치고, 흔한 상용구보다 특이한 법률 용어에 가중치를 더 준다).
 *
 * 처음엔 Gemini API를 불렀는데, Cloudflare Worker는 요청마다 전 세계 아무
 * 데이터센터에서나 실행될 수 있어서 그중 Gemini가 막아둔 지역(예: 유럽)에서
 * 실행되면 "User location is not supported" 400 에러가 났다(실측: 4번 중
 * 3번 실패). Cloudflare Workers AI(env.AI)는 Cloudflare 자체 인프라 안에서
 * 도는 별도 계정/키 없이 쓰는 서비스라 이 지역 차단 문제 자체가 없다 -
 * 그래서 Gemini 대신 이걸로 바꿨다(wrangler.toml의 [ai] binding 참고).
 *
 * contract_law_corpus.json은 매번 GitHub Pages에서 그대로 fetch해 온다
 * (빌드 시 번들링하지 않음) - 법령 데이터가 갱신될 때마다 이 Worker를
 * 재배포할 필요가 없도록.
 *
 * 이 Worker에는 그 뒤로 라우트가 두 개 더 늘었다:
 * - POST /titles: 담기(카트) 항목의 기사 원문 제목을 대신 가져와준다(2026-09-30).
 * - GET  /link: 법제처 API 링크에 필요한 OC 인증키를 커밋되는 데이터에 노출하지
 *   않기 위한 리디렉션 라우트(2026-10-01, 보안 리뷰로 발견된 키 노출 사고 대응).
 * 자세한 설명은 각 핸들러 함수 위 주석 참고.
 */

const ALLOWED_ORIGIN = "https://jshssysh.github.io";
const LAW_DATA_URL = "https://jshssysh.github.io/news-dashboard/contract_law_corpus.json";

// CORS의 Access-Control-Allow-Origin은 브라우저가 "응답을 읽게 해줄지"만 정하는
// 브라우저 쪽 약속이라, curl 등으로 직접 호출하면 전혀 막지 못한다(이 Worker의
// URL은 html_template.html에 평문으로 박혀있어 사이트 소스보기만 해도 알 수 있음
// - 2026-10-01, 코드 리뷰로 발견). 완벽한 인증은 아니지만, Origin 헤더가 "있는데"
// 우리 사이트가 아니면 거부해서 최소한 캐주얼한 남용(스크립트 긁어가기, 다른
// 사이트가 방문자 브라우저를 통해 이 Worker를 대신 호출하는 것)은 막는다 - 헤더
// 자체를 위조하는 공격자는 못 막지만, 그 정도 비용을 지불할 가치가 없게 만든다.
function isAllowedOrigin(request) {
  const origin = request.headers.get("Origin");
  return !origin || origin === ALLOWED_ORIGIN;
}
const MAX_TEXT_LENGTH = 8000;
// 예전엔 40개 x 500자였는데, 아래 selectTopCandidates 교체와 함께 늘렸다 -
// 실측(사내 실제 계약서 문구로 테스트) 상 정답 조문("하도급법 제3조의4
// 부당한 특약의 금지" 같은 짧고 원론적인 금지조항)이 40위 안에 못 들고
// 80~90위대까지 밀리는 경우가 있어서, 조문당 글자 수를 줄이는 대신 후보
// 개수를 늘려 그 순위대까지 커버한다 (90개 x 300자 = 27,000자로, 이전
// 20,000자보다는 늘었지만 모델의 24,000토큰 컨텍스트 안에는 여전히 여유가
// 있다 - 한글은 토큰당 1자 미만인 경우가 흔해 다소 빡빡할 수 있으니 문구가
// 길 땐 추후 실측하며 조정).
const TOP_CANDIDATE_COUNT = 90;
const ARTICLE_SNIPPET_LENGTH = 300;
// @cf/meta/llama-3.1-8b-instruct는 2026-05-30 deprecated돼 실제로 호출 실패가
// 났고(실측), 후속 -fast 버전은 JSON Mode를 켜도 문법을 깨뜨리는 경우가
// 실측됐다(8B급이라 스키마 준수력이 떨어지는 듯) - 70B 모델로 올려 안정성을
// 높인다(developers.cloudflare.com/workers-ai/json-mode/에 JSON Mode 지원
// 모델로 3.1/3.3 계열이 함께 명시돼 있음).
const AI_MODEL = "@cf/meta/llama-3.3-70b-instruct-fp8-fast";

function corsHeaders() {
  return {
    "Access-Control-Allow-Origin": ALLOWED_ORIGIN,
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
  };
}

function jsonResponse(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { ...corsHeaders(), "Content-Type": "application/json; charset=utf-8" },
  });
}

export default {
  async fetch(request, env) {
    const { pathname, searchParams } = new URL(request.url);

    // 브라우저 주소창/<a href> 클릭으로 바로 이동하는 GET 라우트라 POST 전용
    // 게이트보다 앞에 둔다(CORS/OPTIONS 프리플라이트 자체가 필요 없는 단순 탐색).
    if (request.method === "GET" && pathname === "/link") {
      return handleLink(searchParams, env);
    }

    if (request.method === "OPTIONS") {
      return new Response(null, { headers: corsHeaders() });
    }
    if (request.method !== "POST") {
      return jsonResponse({ error: "POST만 지원합니다" }, 405);
    }
    if (!isAllowedOrigin(request)) {
      return jsonResponse({ error: "허용되지 않은 origin입니다" }, 403);
    }

    if (pathname === "/titles") {
      return handleTitles(request);
    }

    let body;
    try {
      body = await request.json();
    } catch (e) {
      return jsonResponse({ error: "요청 본문이 JSON이 아닙니다" }, 400);
    }

    const text = (body.text || "").trim();
    if (!text) return jsonResponse({ error: "text가 비어있습니다" }, 400);
    if (text.length > MAX_TEXT_LENGTH) {
      return jsonResponse({ error: `문구가 너무 깁니다 (최대 ${MAX_TEXT_LENGTH}자)` }, 400);
    }

    let lawRows;
    try {
      const res = await fetch(LAW_DATA_URL, { cf: { cacheTtl: 3600, cacheEverything: true } });
      if (!res.ok) throw new Error(`status ${res.status}`);
      lawRows = await res.json();
    } catch (e) {
      return jsonResponse({ error: "법령 데이터를 불러오지 못했습니다" }, 502);
    }

    const candidates = selectTopCandidates(text, lawRows, TOP_CANDIDATE_COUNT);

    try {
      const matches = await callWorkersAI(text, candidates, env);
      return jsonResponse({ matches });
    } catch (e) {
      return jsonResponse({ error: `AI 호출 실패: ${e.message}` }, 502);
    }
  },
};

// 처음엔 한글 2글자 이상 "단어" 겹침 개수로 점수를 매겼는데(공백 기준
// 아님, 정규식 부분열), 한국어는 조사가 어간에 그대로 붙어서("하도급대금을"
// vs "하도급대금") 계약서 문구와 법조문에서 같은 말이어도 토큰이 어긋나는
// 경우가 실측됐고, 단순 겹침 개수는 그냥 길고 같은 말을 여러 번 반복하는
// 조문에 유리해서 - 정작 "부당한 특약의 금지"처럼 짧고 원론적인 금지조항이
// 후보 40위 안에 못 들고 652위까지 밀리는 게 실측됐다(2026-09-14, 실제
// 계약서 문구로 테스트).
//
// 조사 문제는 형태소 분석 없이 글자 2-gram(음절 슬라이딩 윈도우)로 비교하면
// 어간이 같으면 대부분의 gram이 겹치므로 완화된다. 길이 편향은 단순 겹침
// 개수 대신, 코퍼스 전체에서 자주 나오는 gram(조사·흔한 법률 상용구 등)의
// 가중치를 낮추는 IDF(역문서빈도)로 완화한다 - 같은 실측 문구로
// 비교했을 때 652위 -> 86위로 개선됨을 확인. (2-gram+IDF에 조문 길이로
// 한 번 더 나누는 정규화도 같이 테스트했으나 이 실측 사례에선 오히려
// 95위로 더 나빠져서 채택하지 않음 - 짧은 원론 조문이 길이 정규화로 더
// 손해를 보는 역효과가 있었다.)
function bigrams(text) {
  const clean = String(text || "").replace(/[^가-힣a-zA-Z0-9]/g, "");
  const grams = [];
  for (let i = 0; i < clean.length - 1; i++) grams.push(clean.slice(i, i + 2));
  return grams;
}

function selectTopCandidates(clauseText, corpus, topN) {
  const clauseGrams = new Set(bigrams(clauseText));
  const articleGramSets = corpus.map((c) => new Set(bigrams(c.text)));

  // 코퍼스 전체 기준 문서빈도(df) - 조사·흔한 상용구처럼 여기저기 다 나오는
  // gram일수록 특정 조문을 가려내는 데 도움이 안 되므로 가중치를 낮춘다.
  const df = new Map();
  for (const gramSet of articleGramSets) {
    for (const g of gramSet) df.set(g, (df.get(g) || 0) + 1);
  }
  const N = corpus.length;
  const idf = (g) => Math.log(N / df.get(g));

  const scored = corpus.map((c, i) => {
    let score = 0;
    for (const g of articleGramSets[i]) {
      if (clauseGrams.has(g)) score += idf(g);
    }
    return { ...c, score };
  });
  scored.sort((a, b) => b.score - a.score);
  const withHits = scored.filter((c) => c.score > 0);
  // 겹치는 gram이 하나도 없으면(전혀 다른 도메인의 문구 등) 그래도 뭔가는
  // 보여주도록 상위 topN을 그냥 채워서 보낸다 - 빈 결과보다 낫다.
  return (withHits.length ? withHits : scored).slice(0, topN);
}

function buildPrompt(clauseText, candidates) {
  const corpusBlock = candidates
    .map((c) => `- [${c.lawAbbr} ${c.article}] ${(c.text || "").slice(0, ARTICLE_SNIPPET_LENGTH)}`)
    .join("\n");
  return `당신은 한국 기업 법무 검토 담당자입니다.
아래 [검토할 계약서 문구]가 [관련 법령 후보] 중 어느 조문과 저촉될 소지가
있는지 판단하세요. 후보에 없는 법령은 언급하지 마세요.

[검토할 계약서 문구]
${clauseText}

[관련 법령 후보]
${corpusBlock}

저촉 소지가 있는 항목만 아래 JSON 형식으로만 응답하세요(다른 설명 문장은
절대 붙이지 말 것, 위반 소지가 전혀 없으면 matches를 빈 배열로):
{"matches": [
  {"law": "법령약칭", "article": "조문", "risk": "상 | 중 | 하", "reason": "왜 저촉 소지가 있는지 한국어 한 문장"}
]}`;
}

// 8B급 모델은 프롬프트 지시만으로는 JSON 문법을 종종 깨뜨려서(실측:
// "Expected ',' or ']'" 파싱 오류) - Workers AI의 JSON Mode(json_schema)로
// 문법 자체를 강제한다. 이 모드를 지원하는 모델만 안전하며(Llama 3.1/3.3
// 계열 포함), 스키마와 다르면 "JSON Mode couldn't be met" 오류가 난다.
const MATCHES_SCHEMA = {
  type: "object",
  properties: {
    matches: {
      type: "array",
      items: {
        type: "object",
        properties: {
          law: { type: "string" },
          article: { type: "string" },
          risk: { type: "string" },
          reason: { type: "string" },
        },
        required: ["law", "article", "risk", "reason"],
      },
    },
  },
  required: ["matches"],
};

async function callWorkersAI(clauseText, candidates, env) {
  const result = await env.AI.run(AI_MODEL, {
    messages: [
      { role: "system", content: "당신은 지시받은 JSON 스키마 형식으로만 응답하는 어시스턴트입니다." },
      { role: "user", content: buildPrompt(clauseText, candidates) },
    ],
    response_format: { type: "json_schema", json_schema: MATCHES_SCHEMA },
  });

  const parsed = parseModelJson(result.response);
  return Array.isArray(parsed?.matches) ? parsed.matches : [];
}

// JSON Mode를 켜도 모델이 이따금 문법을 깨뜨리는 걸 실측했다(예: 배열 원소
// 사이 콤마 누락) - env.AI.run이 이미 파싱된 객체를 줄 수도 있고 문자열을
// 줄 수도 있어 그 경우부터 처리하고, 문자열이 곧바로 안 읽히면 첫 '{'~
// 마지막 '}'만 잘라 한 번 더 시도한다(완전히 실패하면 원문 일부를 에러에
// 남겨 다음에 원인을 바로 볼 수 있게 한다).
function parseModelJson(response) {
  if (response && typeof response === "object") return response;
  const raw = String(response || "").trim();
  try {
    return JSON.parse(raw);
  } catch (e) {
    const start = raw.indexOf("{");
    const end = raw.lastIndexOf("}");
    if (start !== -1 && end !== -1) {
      try {
        return JSON.parse(raw.slice(start, end + 1));
      } catch (e2) {
        // 아래에서 원본 오류와 함께 던진다.
      }
    }
    throw new Error(`JSON 파싱 실패 (${e.message}): ${raw.slice(0, 200)}`);
  }
}

/**
 * "내보내기" 표에 담긴 기사 제목이 네이버 뉴스검색 API 응답 자체에서 이미
 * "..."로 잘려서 오는 문제(2026-09-30, 사용자 실측 - 예: 한 기사는 news_list.csv에
 * 이미 "...무너진 정의 다시 세울 것..." 처럼 끝이 잘려 저장돼 있는데, 같은
 * 발언을 다룬 다른 언론사 기사 제목들은 "...무너진 정의 다시 세울 것""처럼
 * 안 잘려 있어 원제목이 있다는 게 확인됨)을 고치기 위한 엔드포인트.
 *
 * 담긴 항목(보통 수~수십 건)에 한해서만, 실제 기사 원문 페이지를 가져와
 * <title> 또는 og:title에서 안 잘린 원제목을 뽑아 돌려준다. 정적 사이트
 * (GitHub Pages)에서 브라우저가 언론사 도메인에 직접 요청하면 대부분
 * CORS에 막히므로, 이 Worker가 서버 쪽에서 대신 요청한다.
 */
const TITLE_FETCH_TIMEOUT_MS = 6000;
// Cloudflare 무료 플랜은 호출 1회당 서브리퀘스트 50개 한도라(코드 리뷰로 확인,
// 2026-10-01), 60이면 뒤쪽 항목이 한도 초과로 조용히 실패할 수 있었다 - 여유를
// 두고 45로 낮춘다. 실제로는 클라이언트가 "..."로 끝난 항목만 골라 보내므로
// (html_template.html의 cartItemNeedsRealTitle 참고) 한 번에 이만큼 몰리는
// 일은 드물다 - 이 상수는 남용성 요청에 대한 방어선일 뿐이다.
const TITLE_FETCH_MAX_ITEMS = 45;
// <head> 안의 title/og:title을 뽑는 데는 본문 전체가 필요 없다 - 응답을 통째로
// 버퍼링하는 대신 이 바이트 수까지만 읽고 끊는다(코드 리뷰로 발견된 "응답 크기
// 제한 없음" 문제 - 인증 없는 라우트와 겹치면 큰 응답을 반복 요청해 Worker
// 자원을 소모시킬 수 있었다).
const MAX_TITLE_FETCH_BYTES = 262144; // 256KB

async function handleTitles(request) {
  let body;
  try {
    body = await request.json();
  } catch (e) {
    return jsonResponse({ error: "요청 본문이 JSON이 아닙니다" }, 400);
  }
  const items = Array.isArray(body.items) ? body.items.slice(0, TITLE_FETCH_MAX_ITEMS) : [];
  if (!items.length) return jsonResponse({ titles: {} });

  const results = await Promise.allSettled(
    items.map(async (item) => {
      const title = await fetchRealTitle(item.url);
      return { id: item.id, title };
    })
  );

  const titles = {};
  for (const r of results) {
    if (r.status === "fulfilled" && r.value.title) titles[r.value.id] = r.value.title;
    // 실패한 항목은 그냥 응답에서 빠진다 - 화면 쪽이 원래 있던(네이버가 잘라준)
    // 제목을 그대로 쓰면 되므로, 실패를 에러로 취급할 필요가 없다.
  }
  return jsonResponse({ titles });
}

async function fetchRealTitle(url) {
  if (!url) return null;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TITLE_FETCH_TIMEOUT_MS);
  try {
    const res = await fetch(url, {
      signal: controller.signal,
      // 일부 언론사 서버가 User-Agent 없는 요청(봇으로 의심)을 차단해서 실측 확인됨 -
      // 일반 브라우저처럼 보이는 값을 붙인다.
      headers: { "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36" },
    });
    if (!res.ok) return null;
    const bytes = await readBoundedBytes(res, MAX_TITLE_FETCH_BYTES);
    if (!bytes) return null;
    const html = decodeHtmlBytes(bytes, res.headers.get("content-type"));
    return extractTitle(html);
  } catch (e) {
    return null; // 타임아웃/네트워크 오류 - 이 기사 하나만 실패, 나머지에 영향 없음
  } finally {
    clearTimeout(timer);
  }
}

// Content-Length를 선언 안 하거나(청크 전송) 거짓으로 적은 서버도 있을 수 있어,
// 헤더만 믿지 않고 스트림을 직접 읽으면서 maxBytes에서 끊는다.
async function readBoundedBytes(response, maxBytes) {
  const declared = response.headers.get("content-length");
  if (declared && Number(declared) > maxBytes * 4) return null; // 명백히 과도하면 스트림도 안 연다
  if (!response.body) return new Uint8Array(await response.arrayBuffer());

  const reader = response.body.getReader();
  const chunks = [];
  let total = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > maxBytes) {
        chunks.push(value.subarray(0, value.byteLength - (total - maxBytes)));
        break;
      }
      chunks.push(value);
    }
  } finally {
    try { await reader.cancel(); } catch (e) { /* 이미 끝난 스트림이면 취소 실패해도 무방 */ }
  }
  const out = new Uint8Array(Math.min(total, maxBytes));
  let offset = 0;
  for (const c of chunks) { out.set(c, offset); offset += c.byteLength; }
  return out;
}

// 일부(특히 오래된/영세) 언론사 사이트가 아직 EUC-KR을 쓴다 - Content-Type
// 헤더나 <meta charset>에서 인코딩을 알아내 그에 맞게 디코딩한다. 못 찾으면
// UTF-8로 가정(대부분의 현대 사이트가 이쪽).
function decodeHtmlBytes(bytes, contentTypeHeader) {
  let charset = null;
  const headerMatch = /charset=([\w-]+)/i.exec(contentTypeHeader || "");
  if (headerMatch) charset = headerMatch[1].toLowerCase();
  if (!charset) {
    const head = new TextDecoder("utf-8", { fatal: false }).decode(bytes.slice(0, 2048));
    const metaMatch = /<meta[^>]+charset=["']?([\w-]+)/i.exec(head);
    if (metaMatch) charset = metaMatch[1].toLowerCase();
  }
  if (charset && charset !== "utf-8" && charset !== "utf8") {
    try {
      return new TextDecoder(charset, { fatal: false }).decode(bytes);
    } catch (e) {
      // TextDecoder가 모르는 이름(오타 등)이면 UTF-8로 폴백
    }
  }
  return new TextDecoder("utf-8", { fatal: false }).decode(bytes);
}

// og:title이 있으면 그걸 우선한다(대부분 언론사가 SNS 공유용으로 이미 안 잘린
// 깔끔한 헤드라인을 넣어둠). 없으면 <title> 태그로 대신하되, 그 경우엔 흔히
// 끝에 붙는 "- 언론사명"/"| 언론사명" 꼬리표를 제거한다(og:title엔 이 꼬리표가
// 거의 없어서 <title> 폴백에서만 처리).
function extractTitle(html) {
  // 따옴표 종류를 캡처해 같은 종류로 닫힐 때까지만 매칭한다(백레퍼런스 \1) -
  // 이전엔 [^"']*로 아무 따옴표에서나 끊겨서, content="공정위, '갑질' 제재"처럼
  // 큰따옴표 안에 작은따옴표가 섞인(한국어 기사 제목에 흔한) 경우 "공정위,"에서
  // 잘려버렸다(코드 리뷰로 발견, 2026-10-01).
  const ogMatch =
    /<meta[^>]+property=["']og:title["'][^>]*content=(["'])(.*?)\1/i.exec(html) ||
    /<meta[^>]+content=(["'])(.*?)\1[^>]*property=["']og:title["']/i.exec(html);
  if (ogMatch && ogMatch[2].trim()) return decodeHtmlEntities(ogMatch[2]).trim();

  const titleMatch = /<title[^>]*>([^<]*)<\/title>/i.exec(html);
  if (titleMatch && titleMatch[1].trim()) {
    const raw = decodeHtmlEntities(titleMatch[1]).trim();
    // 구분자 양쪽에 공백이 있을 때만 "꼬리표"로 본다 - 공백 없이 붙은 하이픈은
    // "삼성-LG 협력 확대"처럼 제목 자체의 일부일 수 있어 건드리지 않는다
    // (이전 정규식은 공백 없어도 잘라내서 이런 제목을 훼손했다 - 코드 리뷰로 발견).
    return raw.replace(/\s+[-|–—:｜]\s+[^-|–—:｜]{1,30}$/, "").trim() || raw;
  }
  return null;
}

/**
 * 법제처 국가법령정보 Open API(www.law.go.kr/DRF)의 의결서/판례/행정규칙 상세는
 * OC 인증키가 있어야 조회되는데, 그동안 collect_decisions.py/collect_law_penalties.py가
 * 이 링크를 "OC=실제키" 형태로 통째로 CSV에 저장해왔다 - 그게 그대로
 * docs/decisions.json 등 공개 GitHub Pages에 실려 나가 실제 발급받은 키가
 * 공개 저장소에 노출된 사고가 있었다(코드 리뷰로 발견, 2026-10-01 - decision_list.csv
 * 484건 전부, "test" 데모키가 아니라 실제 키였음). OC는 이제 이 Worker의 시크릿
 * (wrangler secret / GitHub Actions secrets의 LAW_GO_KR_OC, deploy-worker.yml 참고)
 * 으로만 보관하고, 커밋되는 데이터에는 "/link?target=...&id=..." 형태로만 남겨
 * 이 라우트가 실제 OC 붙은 주소로 리디렉션하게 한다.
 */
const LINK_TARGETS = new Set(["ftc", "prec", "admrul"]);
// 법제처가 내려주는 일련번호는 보통 숫자뿐이지만, 형식이 바뀔 경우에 대비해
// 영숫자와 일부 구두점까지만 허용하고 그 외 문자가 있으면 바로 거부한다.
const SAFE_LAW_ID = /^[A-Za-z0-9_.-]{1,40}$/;

async function handleLink(searchParams, env) {
  const target = searchParams.get("target");
  const id = searchParams.get("id");
  if (!LINK_TARGETS.has(target) || !id || !SAFE_LAW_ID.test(id)) {
    return new Response("잘못된 요청입니다", { status: 400 });
  }
  if (!env.LAW_GO_KR_OC) {
    return new Response("서버 설정 오류 - OC 시크릿이 등록돼 있지 않습니다", { status: 500 });
  }
  const url = `https://www.law.go.kr/DRF/lawService.do?OC=${encodeURIComponent(env.LAW_GO_KR_OC)}&target=${target}&ID=${encodeURIComponent(id)}&type=HTML`;
  return Response.redirect(url, 302);
}

const HTML_ENTITIES = {
  amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: " ", middot: "·",
  ldquo: "“", rdquo: "”", lsquo: "‘", rsquo: "’", hellip: "…", mdash: "—", ndash: "–",
};
function decodeHtmlEntities(s) {
  return String(s || "").replace(/&(#x?[0-9a-fA-F]+|[a-zA-Z]+);/g, (m, ent) => {
    if (ent[0] === "#") {
      const code = ent[1] === "x" || ent[1] === "X" ? parseInt(ent.slice(2), 16) : parseInt(ent.slice(1), 10);
      return Number.isFinite(code) ? String.fromCodePoint(code) : m;
    }
    return HTML_ENTITIES[ent] ?? m;
  });
}
