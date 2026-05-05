import { useMemo, type ReactElement } from 'react';
import {
  MdWbSunny,
  MdWaterDrop,
  MdAttachMoney,
  MdEco,
  MdInsertDriveFile,
  MdMemory,
  MdGavel,
  MdAgriculture,
  MdAutoAwesome,
} from 'react-icons/md';
import type { ToolCall } from '@/hooks/useFarmAgent';

// ── ToolOutputCard ───────────────────────────────────────────────────────────
//
// 백엔드 도구가 내놓는 결과를 도메인별로 구조화해 보여준다. 기존 ReasoningTrace
// 는 `pre`로 raw JSON 만 토했는데, 사용자는 그걸 못 읽는다. 여기서 도구 이름을
// 보고 적절한 카드(날씨 → 위험 행렬, 직불 → 자격 칩, 진단 → 신뢰도 바)로
// 변환한다. 알려진 포맷이 아니면 fallback으로 깔끔한 JSON 트리.

type ParsedJson = unknown;

function tryParse(output?: string): ParsedJson | undefined {
  if (!output) return undefined;
  const trimmed = output.trim();
  if (!trimmed.startsWith('{') && !trimmed.startsWith('[')) return undefined;
  try {
    return JSON.parse(trimmed);
  } catch {
    return undefined;
  }
}

function isObj(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

export function ToolOutputCard({ call }: { call: ToolCall }) {
  const parsed = useMemo(() => tryParse(call.output), [call.output]);

  if (!call.output) return null;

  const renderer = pickRenderer(call.name);
  if (renderer && parsed !== undefined) {
    return <div className="space-y-1.5">{renderer(parsed)}</div>;
  }

  // Fallback: structured JSON or plain text
  if (parsed !== undefined) {
    return <JsonView value={parsed} />;
  }
  return (
    <pre className="max-h-40 overflow-y-auto whitespace-pre-wrap break-words font-mono text-[11px] leading-relaxed text-gray-700">
      {call.output}
    </pre>
  );
}

// ── domain renderers ────────────────────────────────────────────────────────

function pickRenderer(name: string): ((v: ParsedJson) => ReactElement) | null {
  switch (name) {
    case 'get_current_weather':
      return renderWeather;
    case 'get_market_prices':
    case 'get_market_prices_for_crop':
      return renderMarket;
    case 'list_eligible_subsidies':
    case 'check_eligibility_rule':
    case 'get_subsidy_details':
      return renderSubsidy;
    case 'search_subsidy_regulations':
      return renderRegulationHits;
    case 'diagnose_pest':
    case 'classify_pest_image':
      return renderPest;
    case 'get_my_farm_profile':
      return renderProfile;
    case 'get_recent_iot_decisions':
      return renderIotDecisions;
    case 'list_journal_entries':
    case 'get_journal_daily_summary':
      return renderJournal;
    default:
      return null;
  }
}

function renderWeather(v: ParsedJson) {
  if (!isObj(v)) return <JsonView value={v} />;
  const temp = v.temperature ?? v.temp ?? v.tmp;
  const humidity = v.humidity ?? v.reh;
  const wind = v.wind_speed ?? v.wsd;
  const sky = v.sky ?? v.condition ?? v.summary;
  const risks = (v.risks ?? v.alerts) as Record<string, unknown>[] | undefined;
  return (
    <div className="rounded-md border border-sky-200 bg-sky-50/50 p-2">
      <div className="mb-1.5 flex items-center gap-1.5 text-[11px] font-bold text-sky-900">
        <MdWbSunny className="text-sm" /> 기상
      </div>
      <div className="grid grid-cols-2 gap-1.5 text-[11px] text-gray-700">
        {temp !== undefined && <Stat label="기온" value={`${temp}°C`} />}
        {humidity !== undefined && <Stat label="습도" value={`${humidity}%`} />}
        {wind !== undefined && <Stat label="풍속" value={`${wind} m/s`} />}
        {sky !== undefined && <Stat label="상태" value={String(sky)} />}
      </div>
      {Array.isArray(risks) && risks.length > 0 && (
        <ul className="mt-1.5 space-y-0.5 border-t border-sky-200 pt-1.5">
          {risks.map((r, i) => (
            <li key={i} className="flex items-center justify-between text-[11px]">
              <span className="text-gray-700">{String(r.type ?? r.name ?? '위험')}</span>
              <span className="font-bold text-rose-600">
                {r.probability !== undefined ? `${Math.round(Number(r.probability) * 100)}%` : ''}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function renderMarket(v: ParsedJson) {
  const items = isObj(v) && Array.isArray(v.items) ? (v.items as Record<string, unknown>[]) : Array.isArray(v) ? (v as Record<string, unknown>[]) : [];
  if (items.length === 0) return <JsonView value={v} />;
  return (
    <div className="rounded-md border border-amber-200 bg-amber-50/50 p-2">
      <div className="mb-1.5 flex items-center gap-1.5 text-[11px] font-bold text-amber-900">
        <MdAttachMoney className="text-sm" /> 시세 ({items.length}건)
      </div>
      <ul className="space-y-0.5 text-[11px] text-gray-700">
        {items.slice(0, 6).map((it, i) => (
          <li key={i} className="flex items-center justify-between">
            <span className="truncate">{String(it.name ?? it.crop ?? it.product ?? '품목')}</span>
            <span className="font-bold text-gray-900">
              {it.price !== undefined ? `${Number(it.price).toLocaleString()}원` : ''}
              {it.unit ? `/${it.unit}` : ''}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function renderSubsidy(v: ParsedJson) {
  const list = isObj(v) && Array.isArray(v.subsidies) ? (v.subsidies as Record<string, unknown>[]) : Array.isArray(v) ? (v as Record<string, unknown>[]) : isObj(v) ? [v] : [];
  if (list.length === 0) return <JsonView value={v} />;
  return (
    <div className="rounded-md border border-emerald-200 bg-emerald-50/50 p-2">
      <div className="mb-1.5 flex items-center gap-1.5 text-[11px] font-bold text-emerald-900">
        <MdGavel className="text-sm" /> 직불·정책 매칭
      </div>
      <ul className="space-y-1 text-[11px]">
        {list.slice(0, 5).map((s, i) => {
          const eligible = s.eligible ?? s.is_eligible;
          return (
            <li key={i} className="flex items-start gap-1.5">
              <span
                className={`mt-0.5 inline-block h-2 w-2 flex-shrink-0 rounded-full ${
                  eligible === true ? 'bg-emerald-500' : eligible === false ? 'bg-rose-400' : 'bg-gray-300'
                }`}
              />
              <div className="min-w-0 flex-1">
                <div className="truncate font-bold text-gray-900">
                  {String(s.name ?? s.title ?? s.subsidy ?? '항목')}
                </div>
                {s.amount !== undefined && (
                  <div className="text-gray-600">최대 {Number(s.amount).toLocaleString()}원</div>
                )}
                {s.reason !== undefined && (
                  <div className="line-clamp-2 text-gray-500">{String(s.reason)}</div>
                )}
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function renderRegulationHits(v: ParsedJson) {
  const hits = isObj(v) && Array.isArray(v.hits) ? (v.hits as Record<string, unknown>[]) : Array.isArray(v) ? (v as Record<string, unknown>[]) : [];
  if (hits.length === 0) return <JsonView value={v} />;
  return (
    <div className="rounded-md border border-indigo-200 bg-indigo-50/40 p-2">
      <div className="mb-1.5 flex items-center gap-1.5 text-[11px] font-bold text-indigo-900">
        <MdInsertDriveFile className="text-sm" /> 시행지침 검색 ({hits.length}건)
      </div>
      <ul className="space-y-1 text-[11px] text-gray-700">
        {hits.slice(0, 4).map((h, i) => (
          <li key={i} className="rounded bg-white/70 p-1.5">
            <div className="font-bold text-indigo-900">
              {String(h.doc ?? h.source ?? '')} {h.clause ? `· ${String(h.clause)}` : ''}
            </div>
            <div className="line-clamp-2 text-gray-600">
              {String(h.snippet ?? h.text ?? h.content ?? '')}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

function renderPest(v: ParsedJson) {
  if (!isObj(v)) return <JsonView value={v} />;
  const name = v.pest ?? v.disease ?? v.label ?? v.name;
  const conf = v.confidence ?? v.probability ?? v.score;
  const confPct = typeof conf === 'number' ? Math.round((conf <= 1 ? conf * 100 : conf)) : null;
  const treatment = v.treatment ?? v.recommendation;
  return (
    <div className="rounded-md border border-rose-200 bg-rose-50/40 p-2">
      <div className="mb-1.5 flex items-center gap-1.5 text-[11px] font-bold text-rose-900">
        <MdEco className="text-sm" /> 진단 결과
      </div>
      {name != null && <div className="text-[12px] font-bold text-gray-900">{String(name)}</div>}
      {confPct !== null && (
        <div className="mt-1">
          <div className="flex items-center justify-between text-[10px] text-gray-600">
            <span>신뢰도</span>
            <span className="font-bold">{confPct}%</span>
          </div>
          <div className="mt-0.5 h-1 w-full overflow-hidden rounded-full bg-gray-200">
            <div
              className={`h-full ${
                confPct >= 80 ? 'bg-emerald-500' : confPct >= 50 ? 'bg-amber-500' : 'bg-rose-500'
              }`}
              style={{ width: `${confPct}%` }}
            />
          </div>
        </div>
      )}
      {treatment != null && (
        <div className="mt-1.5 text-[11px] text-gray-700">
          <span className="font-bold">처방: </span>
          {String(treatment)}
        </div>
      )}
    </div>
  );
}

function renderProfile(v: ParsedJson) {
  if (!isObj(v)) return <JsonView value={v} />;
  return (
    <div className="rounded-md border border-lime-200 bg-lime-50/50 p-2">
      <div className="mb-1.5 flex items-center gap-1.5 text-[11px] font-bold text-lime-900">
        <MdAgriculture className="text-sm" /> 농장 프로필
      </div>
      <div className="grid grid-cols-2 gap-1 text-[11px] text-gray-700">
        {Object.entries(v)
          .filter(([, val]) => typeof val !== 'object' || val === null)
          .slice(0, 8)
          .map(([k, val]) => (
            <Stat key={k} label={k} value={val == null ? '-' : String(val)} />
          ))}
      </div>
    </div>
  );
}

function renderIotDecisions(v: ParsedJson) {
  const list = isObj(v) && Array.isArray(v.decisions) ? (v.decisions as Record<string, unknown>[]) : Array.isArray(v) ? (v as Record<string, unknown>[]) : [];
  if (list.length === 0) return <JsonView value={v} />;
  return (
    <div className="rounded-md border border-cyan-200 bg-cyan-50/40 p-2">
      <div className="mb-1.5 flex items-center gap-1.5 text-[11px] font-bold text-cyan-900">
        <MdMemory className="text-sm" /> 자율 제어 이력 ({list.length})
      </div>
      <ul className="space-y-0.5 text-[11px] text-gray-700">
        {list.slice(0, 5).map((d, i) => (
          <li key={i} className="flex items-center justify-between">
            <span className="truncate">{String(d.action ?? d.summary ?? d.type ?? '제어')}</span>
            <span className="text-[10px] text-gray-500">
              {d.timestamp ? new Date(String(d.timestamp)).toLocaleString('ko-KR', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : ''}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function renderJournal(v: ParsedJson) {
  const list = isObj(v) && Array.isArray(v.entries) ? (v.entries as Record<string, unknown>[]) : Array.isArray(v) ? (v as Record<string, unknown>[]) : isObj(v) ? [v] : [];
  if (list.length === 0) return <JsonView value={v} />;
  return (
    <div className="rounded-md border border-violet-200 bg-violet-50/40 p-2">
      <div className="mb-1.5 flex items-center gap-1.5 text-[11px] font-bold text-violet-900">
        <MdAutoAwesome className="text-sm" /> 영농일지 ({list.length})
      </div>
      <ul className="space-y-0.5 text-[11px] text-gray-700">
        {list.slice(0, 4).map((e, i) => (
          <li key={i} className="line-clamp-2">
            <span className="font-bold text-gray-900">
              {e.date ? String(e.date) + ' · ' : ''}
            </span>
            {String(e.summary ?? e.content ?? e.text ?? '')}
          </li>
        ))}
      </ul>
    </div>
  );
}

// ── helpers ─────────────────────────────────────────────────────────────────

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-2 rounded bg-white/70 px-1.5 py-0.5">
      <span className="truncate text-gray-500">{label}</span>
      <span className="truncate font-bold text-gray-900">{value}</span>
    </div>
  );
}

function JsonView({ value }: { value: unknown }) {
  return (
    <pre className="max-h-40 overflow-y-auto whitespace-pre-wrap break-words rounded-md bg-gray-50 p-1.5 font-mono text-[11px] leading-relaxed text-gray-700">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

// also exported helper for water-related future use
export const _icons = { MdWaterDrop };
