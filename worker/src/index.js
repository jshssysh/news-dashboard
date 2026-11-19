/**
 * news-dashboard 보조 Worker. GitHub Pages 정적 사이트라 서버가 없어서, 서버가 꼭 필요한
 * 두 가지만 여기서 한다.
 * - POST /titles: 담기(카트) 항목의 기사 원문 제목을 대신 가져와준다(2026-09-30).
 * - GET  /link: 법제처 API 링크에 필요한 OC 인증키를 커밋되는 데이터에 노출하지
 *   않기 위한 리디렉션 라우트(2026-10-01).
 * 예전에 있던 계약서 문구 AI 검토(POST /)는 2026-11-19에 뺐다(입력 문구를 외부 AI로
 * 보내는 기능이라). 자세한 설명은 각 핸들러 함수 위 주석 참고.
 */

const ALLOWED_ORIGIN = "https://jshssysh.github.io";

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

    return jsonResponse({ error: "없는 경로입니다" }, 404);
  },
};

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
