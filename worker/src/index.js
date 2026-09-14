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
 */

const ALLOWED_ORIGIN = "https://jshssysh.github.io";
const LAW_DATA_URL = "https://jshssysh.github.io/news-dashboard/contract_law_corpus.json";
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
    if (request.method === "OPTIONS") {
      return new Response(null, { headers: corsHeaders() });
    }
    if (request.method !== "POST") {
      return jsonResponse({ error: "POST만 지원합니다" }, 405);
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
