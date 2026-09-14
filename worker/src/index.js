/**
 * news-dashboard의 "법령" 탭에서 붙여넣은 계약서 문구를, 이미 수집돼 있는
 * 공정위 소관 법령 + 상법 벌칙/과징금 조문(law_penalties.json)과 대조해
 * 저촉 소지가 있는 조문을 AI로 판단해준다.
 *
 * 이 프로젝트(news-dashboard)는 GitHub Pages 정적 사이트라 서버가 없어서,
 * "사용자가 그 순간 입력하는 임의의 텍스트"를 실시간으로 AI 검토하려면
 * 별도의 서버가 필요하다 - 이 Worker가 그 역할이다 (2026-09-14, 계약서
 * 조항 저촉 확인 기능 논의 결과).
 *
 * 처음엔 Gemini API를 불렀는데, Cloudflare Worker는 요청마다 전 세계 아무
 * 데이터센터에서나 실행될 수 있어서 그중 Gemini가 막아둔 지역(예: 유럽)에서
 * 실행되면 "User location is not supported" 400 에러가 났다(실측: 4번 중
 * 3번 실패). Cloudflare Workers AI(env.AI)는 Cloudflare 자체 인프라 안에서
 * 도는 별도 계정/키 없이 쓰는 서비스라 이 지역 차단 문제 자체가 없다 -
 * 그래서 Gemini 대신 이걸로 바꿨다(wrangler.toml의 [ai] binding 참고).
 *
 * law_penalties.json은 매번 GitHub Pages에서 그대로 fetch해 온다(빌드 시
 * 번들링하지 않음) - 법령탭 데이터가 갱신될 때마다 이 Worker를 재배포할
 * 필요가 없도록.
 */

const ALLOWED_ORIGIN = "https://jshssysh.github.io";
const LAW_DATA_URL = "https://jshssysh.github.io/news-dashboard/law_penalties.json";
const MAX_TEXT_LENGTH = 8000;
// @cf/meta/llama-3.1-8b-instruct는 2026-05-30 deprecated돼 실제로 호출 실패가
// 났다(실측, developers.cloudflare.com/workers-ai/models/ 확인) - 그
// 후속으로 안내되는 -fast 버전을 쓴다.
const AI_MODEL = "@cf/meta/llama-3.1-8b-instruct-fast";

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

    // 프롬프트 길이를 감안해 조문 본문은 앞부분만 잘라 후보로 넣는다.
    const corpus = lawRows.map((r) => ({
      law: r.lawAbbr,
      article: r.article,
      kind: r.kind,
      summary: (r.requirement || "").slice(0, 300),
    }));

    try {
      const matches = await callWorkersAI(text, corpus, env);
      return jsonResponse({ matches });
    } catch (e) {
      return jsonResponse({ error: `AI 호출 실패: ${e.message}` }, 502);
    }
  },
};

function buildPrompt(clauseText, corpus) {
  const corpusBlock = corpus
    .map((c) => `- [${c.law} ${c.article} / ${c.kind}] ${c.summary}`)
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

async function callWorkersAI(clauseText, corpus, env) {
  const result = await env.AI.run(AI_MODEL, {
    messages: [
      { role: "system", content: "당신은 지시받은 JSON 스키마 형식으로만 응답하는 어시스턴트입니다." },
      { role: "user", content: buildPrompt(clauseText, corpus) },
    ],
    response_format: { type: "json_schema", json_schema: MATCHES_SCHEMA },
  });

  // env.AI.run이 이미 파싱된 객체를 줄 수도, 문자열을 줄 수도 있어 방어적으로 처리한다.
  const parsed = typeof result.response === "string" ? JSON.parse(result.response) : result.response;
  return Array.isArray(parsed?.matches) ? parsed.matches : [];
}
