/**
 * 공익직불 (정부 지원금) API 클라이언트.
 *
 * 백엔드 엔드포인트:
 *   GET  /api/v1/subsidy/match              자격 매칭 (eligible/ineligible/needs_review)
 *   POST /api/v1/subsidy/ask                자연어 질의응답 (RAG + LLM)
 *   GET  /api/v1/subsidy/detail/{code}      지원금 상세 정보
 */

const API_BASE = 'http://localhost:8000/api/v1/subsidy';
const AUTH_BASE = 'http://localhost:8000/api/v1/auth';

/**
 * 만료된 access_token 을 refresh_token 으로 갱신.
 * AuthContext 의 startRefreshTimer 가 55분마다 갱신하지만, 백엔드 재시작이나
 * 시계 차이 등으로 인해 그 사이에도 401 이 발생할 수 있다. 이 함수는 개별 요청이
 * 401 일 때 즉시 갱신을 시도해 사용자가 강제 로그아웃되지 않게 한다.
 */
async function tryRefreshToken(): Promise<boolean> {
  try {
    const res = await fetch(`${AUTH_BASE}/refresh`, {
      method: 'POST',
      credentials: 'include',
    });
    return res.ok;
  } catch {
    return false;
  }
}

export type EligibilityStatus = 'eligible' | 'ineligible' | 'needs_review';

export interface Reason {
  text: string;
  source: string | null;
}

export interface SourceClause {
  tag: string;
  text: string;
  snippet: string | null;
}

export interface PaymentStep {
  description: string;
  amount_krw: number;
}

export interface PaymentCalculation {
  total_krw: number;
  steps: PaymentStep[];
  note: string | null;
}

export interface EligibilityResult {
  subsidy_code: string;
  subsidy_name: string;
  status: EligibilityStatus;
  reasons: Reason[];
  estimated_amount_krw: number | null;
  source_articles: string[];
  source_clauses: SourceClause[];
  payment_calculation: PaymentCalculation | null;
}

export interface MatchResponse {
  user_id: string;
  eligible: EligibilityResult[];
  ineligible: EligibilityResult[];
  needs_review: EligibilityResult[];
}

export interface Citation {
  article: string;
  chapter: string;
  snippet: string;
  similarity: number;
}

/**
 * 대화 한 턴. assistant 답변에는 citations 도 함께 보관해 UI 가 매 답변마다
 * 독립적으로 펼칠 수 있도록 한다. 백엔드 history 전송 시 citations 는 빼고
 * role/content 만 보낸다 (백엔드 ChatTurn 스키마에 맞춤).
 *
 * faithfulnessWarnings: 답변에 등장하는 조항 태그 중 검색된 citation 으로 검증되지
 * 않는 것의 리스트 (예: ["II-7"]). 비어 있으면 모든 인용이 검증됨.
 */
export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  citations?: Citation[];
  escalationNeeded?: boolean;
  faithfulnessWarnings?: string[];
  isStreaming?: boolean;
}

// ── SSE event 타입 (백엔드 /ask 가 emit) ───────────────────

export type AskStreamEvent =
  | { type: 'delta'; content: string }
  | {
      type: 'done';
      citations: Citation[];
      escalation_needed: boolean;
      faithfulness_warnings: string[];
    }
  | { type: 'error'; message: string };

export interface SubsidyDetail {
  id: number;
  code: string;
  name_ko: string;
  category: string;
  description: string;
  min_area_ha: number;
  max_area_ha: number | null;
  requires_promotion_area: boolean | null;
  requires_farm_registration: boolean;
  min_rural_residence_years: number;
  min_farming_years: number;
  eligible_farmland_types: string[];
  eligible_farmer_types: string[];
  payment_structure: Record<string, unknown>;
  source_articles: string[];
  payment_amount_krw: number | null;
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      ...(options.headers || {}),
    },
    ...options,
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`${response.status}: ${text || response.statusText}`);
  }
  return response.json() as Promise<T>;
}

export function fetchMatch(): Promise<MatchResponse> {
  return request<MatchResponse>('/match');
}

/**
 * 백엔드 /ask 에 SSE 스트리밍으로 질문을 보낸다. AsyncGenerator 를 반환하므로
 * 호출자는 `for await (const ev of askSubsidyStream(...))` 로 이벤트를 소비.
 *
 * 이벤트 흐름:
 *   delta × N → done × 1     (정상)
 *   delta × N → error × 1    (LLM 실패 — 부분 답변 유지)
 *   error                    (사전 단계 실패)
 *
 * citations 와 faithfulness_warnings 는 done 이벤트로만 도착한다 (LLM 답변이
 * 끝난 뒤에야 검증 가능하므로 마지막 frame).
 */
export async function* askSubsidyStream(
  question: string,
  options: { history?: ChatMessage[]; subsidyCode?: string; signal?: AbortSignal } = {},
): AsyncGenerator<AskStreamEvent, void, void> {
  const history = (options.history ?? []).map(({ role, content }) => ({ role, content }));
  const body = JSON.stringify({
    question,
    subsidy_code: options.subsidyCode,
    history,
  });

  // 60s timeout — RAG(~3s) + LLM streaming(~5-15s) 정상 응답 범위 밖이면 실패시킨다.
  const timeoutCtl = new AbortController();
  const timeoutId = setTimeout(() => timeoutCtl.abort(), 60_000);
  const compositeSignal = options.signal
    ? composeSignals([options.signal, timeoutCtl.signal])
    : timeoutCtl.signal;

  // 첫 시도. 401 이면 refresh-token 으로 access_token 갱신 후 1회 재시도.
  // 갱신·재시도까지 실패하면 사용자에게 "로그인 필요" 안내.
  const doFetch = (): Promise<Response> =>
    fetch(`${API_BASE}/ask`, {
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'text/event-stream',
      },
      body,
      signal: compositeSignal,
    });

  let response: Response;
  try {
    response = await doFetch();
    if (response.status === 401) {
      // 401 → access_token 갱신 시도. 갱신 OK 면 즉시 재요청.
      const refreshed = await tryRefreshToken();
      if (refreshed) {
        response = await doFetch();
      }
    }
  } catch (e) {
    clearTimeout(timeoutId);
    if (timeoutCtl.signal.aborted) {
      throw new Error(
        '백엔드 응답 대기 시간 초과. 백엔드 서버가 실행 중인지 확인해주세요.',
      );
    }
    throw e;
  }

  if (!response.ok || !response.body) {
    clearTimeout(timeoutId);
    const text = await response.text().catch(() => '');
    if (response.status === 401) {
      throw new Error(
        '로그인이 만료되었습니다. 다시 로그인 후 시도해주세요.',
      );
    }
    if (response.status === 403) {
      throw new Error('이 기능에 접근할 권한이 없습니다.');
    }
    throw new Error(`${response.status}: ${text || response.statusText}`);
  }

  // Content-Type 가드: 백엔드가 옛 JSON 엔드포인트라면 'application/json'.
  // SSE 파서는 \n\n 을 기다려 영원히 hang 하므로 즉시 실패시킨다.
  const ct = response.headers.get('Content-Type') ?? '';
  if (!ct.includes('text/event-stream')) {
    clearTimeout(timeoutId);
    const sample = await response.text().catch(() => '');
    throw new Error(
      `백엔드가 SSE 가 아닌 응답을 반환했습니다 (Content-Type: ${ct || '없음'}). ` +
        `백엔드를 새 코드로 재시작해주세요. ` +
        (sample ? `응답 일부: ${sample.slice(0, 200)}` : ''),
    );
  }

  try {
    yield* parseSseStream(response.body);
  } finally {
    clearTimeout(timeoutId);
  }
}

/**
 * 여러 AbortSignal 을 하나로 합치는 헬퍼.
 * AbortSignal.any 가 있으면 그걸 쓰고, 구형 환경에선 직접 합성 (Safari 16 미만 등).
 */
function composeSignals(signals: AbortSignal[]): AbortSignal {
  // @ts-expect-error — AbortSignal.any 는 최신 표준 (TS 타입에 따라 미정의일 수 있음)
  if (typeof AbortSignal.any === 'function') return AbortSignal.any(signals);
  const ctl = new AbortController();
  for (const s of signals) {
    if (s.aborted) {
      ctl.abort(s.reason);
      break;
    }
    s.addEventListener('abort', () => ctl.abort(s.reason), { once: true });
  }
  return ctl.signal;
}

/**
 * SSE 청크를 한 줄씩 파싱해 JSON 페이로드를 yield 한다.
 * EventSource 가 POST 를 지원하지 않아 fetch+ReadableStream 으로 직접 처리.
 *
 * SSE 와이어 포맷:
 *   data: {...}\n
 *   data: {...}\n
 *   \n              ← 이벤트 종료 (빈 줄)
 *
 * sse-starlette 는 한 이벤트 = 한 줄 ("data: {...}\n\n") 로 emit 하므로
 * 이중 개행을 경계로 split 하면 충분하다. event-name (event: ...) 행은 무시.
 */
// SSE event separator: spec allows \r\n\r\n (CRLF, what sse-starlette uses), \n\n (LF), or \r\r (CR).
// 한 가지만 보면 sse-starlette 의 \r\n\r\n 을 놓쳐 frames=0 으로 영원히 hang 한다.
const SSE_FRAME_SEP = /\r\n\r\n|\n\n|\r\r/;

async function* parseSseStream(
  body: ReadableStream<Uint8Array>,
): AsyncGenerator<AskStreamEvent, void, void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let chunkCount = 0;
  let frameCount = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      // Some servers (or proxies) omit the final \r\n\r\n — try to flush a trailing data line.
      if (buffer.trim()) {
        const dataLine = buffer.split(/\r?\n/).find((line) => line.startsWith('data:'));
        if (dataLine) {
          const json = dataLine.slice(5).trim();
          if (json) {
            try {
              frameCount++;
              yield JSON.parse(json) as AskStreamEvent;
            } catch (err) {
              console.warn('[SSE] tail JSON parse 실패:', json, err);
            }
          }
        }
      }
      // 0-frame stream is the symptom of a parser/format mismatch. Surface immediately so
      // future regressions don't hang silently like the original \n\n vs \r\n\r\n bug.
      if (frameCount === 0 && chunkCount > 0) {
        console.warn(
          `[SSE] stream ended with 0 frames despite ${chunkCount} chunks (${buffer.length} bytes left). ` +
            `Sample: ${buffer.slice(0, 120).replace(/\r/g, '\\r').replace(/\n/g, '\\n')}`,
        );
      }
      return;
    }
    chunkCount++;
    buffer += decoder.decode(value, { stream: true });
    // \r\n\r\n / \n\n / \r\r 경계로 frame 분리. 마지막 조각은 다음 read 까지 buffer 에 둔다.
    let m: RegExpExecArray | null;
    while ((m = SSE_FRAME_SEP.exec(buffer)) !== null) {
      const sepIdx = m.index;
      const sepLen = m[0].length;
      const frame = buffer.slice(0, sepIdx);
      buffer = buffer.slice(sepIdx + sepLen);
      // SSE field lines can also be split with \r\n; split tolerantly.
      const dataLine = frame.split(/\r?\n/).find((line) => line.startsWith('data:'));
      if (!dataLine) continue;
      const json = dataLine.slice(5).trim();
      if (!json) continue;
      try {
        frameCount++;
        yield JSON.parse(json) as AskStreamEvent;
      } catch (err) {
        console.warn('[SSE] JSON parse 실패:', json, err);
      }
    }
  }
}

/**
 * detail drawer 가 열릴 때 호출 — 시행지침 원문 발췌를 lazy 로 받아온다.
 * /match 응답에는 snippet 이 비어 있고, 이 호출의 결과로만 채워진다.
 * 응답 map 에 없는 tag 는 조회 실패 (RAG 사용 불가 등) — UI 는 chip-only 로 폴백.
 */
export function fetchClauseSnippets(
  items: { tag: string; text: string }[],
): Promise<{ snippets: Record<string, string> }> {
  return request<{ snippets: Record<string, string> }>('/clauses/snippets', {
    method: 'POST',
    body: JSON.stringify({ items }),
  });
}

// NOTE: /subsidy/detail/{code} 엔드포인트는 백엔드에서 제공되고 있으나
// 현재 UI 는 /match 응답의 데이터만으로 드로어를 그리므로 클라이언트 헬퍼는 미사용.
// 추후 상세 정보(payment_structure 원문 등)를 드로어에 노출하게 되면 복원.
