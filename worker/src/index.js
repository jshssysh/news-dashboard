/**
 * news-dashboard의 "법령" 탭에서 붙여넣은 계약서 문구를, 이미 수집돼 있는
 * 공정위 소관 법령 + 상법 벌칙/과징금 조문(law_penalties.json)과 대조해
 * 저촉 소지가 있는 조문을 Gemini로 판단해준다.
 *
 * 이 프로젝트(news-dashboard)는 GitHub Pages 정적 사이트라 서버가 없어서,
 * "사용자가 그 순간 입력하는 임의의 텍스트"를 실시간으로 AI 검토하려면
 * Gemini 키를 쥐고 있을 별도의 서버가 필요하다 - 이 Worker가 그 역할이다
 * (2026-09-14, 계약서 조항 저촉 확인 기능 논의 결과).
 *
 * law_penalties.json은 매번 GitHub Pages에서 그대로 fetch해 온다(빌드 시
 * 번들링하지 않음) - 법령탭 데이터가 갱신될 때마다 이 Worker를 재배포할
 * 필요가 없도록.
 */

const ALLOWED_ORIGIN = "https://jshssysh.github.io";
const LAW_DATA_URL = "https://jshssysh.github.io/news-dashboard/law_penalties.json";
const MAX_TEXT_LENGTH = 8000;
const GEMINI_MODEL = "gemini-3.5-flash-lite";

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

    // Gemini 프롬프트 길이를 감안해 조문 본문은 앞부분만 잘라 후보로 넣는다.
    const corpus = lawRows.map((r) => ({
      law: r.lawAbbr,
      article: r.article,
      kind: r.kind,
      summary: (r.requirement || "").slice(0, 300),
    }));

    if (!env.GEMINI_API_KEY) {
      return jsonResponse({ error: "서버에 GEMINI_API_KEY가 설정돼 있지 않습니다" }, 500);
    }

    try {
      const matches = await callGemini(text, corpus, env.GEMINI_API_KEY);
      return jsonResponse({ matches });
    } catch (e) {
      return jsonResponse({ error: `Gemini 호출 실패: ${e.message}` }, 502);
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

저촉 소지가 있는 항목만 아래 JSON 배열 형식으로 응답하세요(위반 소지가
전혀 없으면 빈 배열 [] 만 출력):
[
  {"law": "법령약칭", "article": "조문", "risk": "상 | 중 | 하", "reason": "왜 저촉 소지가 있는지 한국어 한 문장"}
]`;
}

async function callGemini(clauseText, corpus, apiKey) {
  const url = `https://generativelanguage.googleapis.com/v1beta/models/${GEMINI_MODEL}:generateContent?key=${apiKey}`;
  const payload = {
    contents: [{ parts: [{ text: buildPrompt(clauseText, corpus) }] }],
    generationConfig: {
      response_mime_type: "application/json",
      thinkingConfig: { thinkingLevel: "low" },
    },
  };
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`status ${res.status}: ${body.slice(0, 200)}`);
  }
  const data = await res.json();
  let raw = data.candidates[0].content.parts[0].text.trim();
  if (raw.startsWith("```json")) raw = raw.slice(7);
  if (raw.startsWith("```")) raw = raw.slice(3);
  if (raw.endsWith("```")) raw = raw.slice(0, -3);
  return JSON.parse(raw.trim());
}
