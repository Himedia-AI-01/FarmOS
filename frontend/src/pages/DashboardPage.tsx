import { useEffect, useMemo } from 'react';
import { Link } from 'react-router-dom';
import { motion } from 'framer-motion';
import {
  MdArrowForward,
  MdAutoAwesome,
  MdCloud,
  MdLockClock,
  MdPayments,
  MdSensors,
  MdShowChart,
  MdThermostat,
  MdWaterDrop,
  MdWbSunny,
  MdWifiOff,
} from 'react-icons/md';
import { useAuth } from '@/context/AuthContext';
import { useAIAgent } from '@/hooks/useAIAgent';
import { useSensorData } from '@/hooks/useSensorData';
import { useFarmAgentContext } from '@/context/FarmAgentContext';
import { AgentMarkdown } from '@/components/agent/AgentMarkdown';
import { EmptyState, Skeleton, StatusDot } from '@/components/ui';

interface CommandLink {
  to: string;
  label: string;
  detail: string;
  illust: string;
}

const COMMAND_LINKS: CommandLink[] = [
  { to: '/iot', label: '시설 제어', detail: '센서·자동화 판단', illust: '/illustrations/module-iot.png' },
  { to: '/diagnosis', label: '진단 워크벤치', detail: '이미지 진단·처방', illust: '/illustrations/module-diagnosis.png' },
  { to: '/journal', label: '영농일지', detail: '음성 기록·통합일지', illust: '/illustrations/module-journal.png' },
  { to: '/weather', label: '기상', detail: '예보 기반 작업 계획', illust: '/illustrations/module-weather.png' },
  { to: '/market', label: '시세', detail: 'KAMIS 가격 변동', illust: '/illustrations/module-market.png' },
  { to: '/subsidy', label: '공익직불', detail: '자격·근거 조항', illust: '/illustrations/module-subsidy.png' },
  { to: '/reviews', label: '판매 인사이트', detail: '리뷰·전략', illust: '/illustrations/module-reviews.png' },
];

const AGENT_ACTIONS = [
  {
    label: '오늘 작업 계획',
    detail: '날씨·시세·일지 종합 우선순위',
    prompt: '오늘 농장에서 해야 할 작업을 우선순위로 정리해줘. 날씨, 최근 IoT 이력, 시세를 함께 고려해서 한국 농민에게 친근한 어투로 알려줘.',
    icon: MdAutoAwesome,
  },
  {
    label: '관수 필요 여부',
    detail: '센서·예보·작물 상태 판단',
    prompt: '현재 토양 습도와 예보를 보고 관수가 필요한지 판단해줘. 필요하면 시간과 양을 권장해줘.',
    icon: MdWaterDrop,
  },
  {
    label: '직불 리스크 점검',
    detail: '자격 가능 여부·근거 인용',
    prompt: '내 농장 정보 기준으로 공익직불 자격 가능 여부와 신청 시 주의사항을 알려줘. 시행지침 근거를 함께 인용해줘.',
    icon: MdPayments,
  },
  {
    label: '출하 타이밍 추천',
    detail: '시세 추세·단기 예보 기반',
    prompt: '주작물의 최근 KAMIS 시세 변동과 단기 예보를 보고 출하 시기를 추천해줘.',
    icon: MdShowChart,
  },
];

function formatTime(value?: string | null) {
  if (!value) return '대기';
  return new Date(value).toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' });
}

function metricValue(value: number | null | undefined, digits = 1) {
  if (value == null) return null;
  return value.toFixed(digits);
}

function controlLabel(controlType?: string) {
  const labels: Record<string, string> = {
    ventilation: '환기',
    irrigation: '관수',
    lighting: '조명',
    shading: '차광/보온',
  };
  return controlType ? labels[controlType] ?? controlType : '판단 없음';
}


export default function DashboardPage() {
  const { user } = useAuth();
  const { connected, latest, alerts, irrigations } = useSensorData();
  const { status, decisions, loading: agentLoading } = useAIAgent();
  const { briefing, briefingLoading, fetchBriefing, sendAndOpen } = useFarmAgentContext();

  useEffect(() => {
    void fetchBriefing(false);
  }, [fetchBriefing]);

  const hasData = Boolean(latest);
  const latestDecision = decisions[0] ?? status?.latest_decision ?? null;
  const agentMode = agentLoading ? '동기화 중' : status?.enabled ? '자율 제어' : status ? '대기' : '미연결';
  const riskCount = useMemo(() => alerts.filter((alert) => !alert.resolved).length, [alerts]);

  const sensorTiles = [
    {
      label: '토양 습도',
      value: metricValue(latest?.soilMoisture),
      unit: '%',
      icon: MdWaterDrop,
      tintClass: 'tint-info',
      iconClass: 'text-[color:var(--color-info)]',
    },
    {
      label: '온도',
      value: metricValue(latest?.temperature),
      unit: '°C',
      icon: MdThermostat,
      tintClass: 'tint-danger',
      iconClass: 'text-[color:var(--color-danger)]',
    },
    {
      label: '대기 습도',
      value: metricValue(latest?.humidity),
      unit: '%',
      icon: MdCloud,
      tintClass: 'tint-success',
      iconClass: 'text-[color:var(--color-primary)]',
    },
    {
      label: '조도',
      value: metricValue(latest?.lightIntensity, 0),
      unit: ' lux',
      icon: MdWbSunny,
      tintClass: 'tint-warning',
      iconClass: 'text-[color:var(--color-accent-dark)]',
    },
  ];

  const greeting = (() => {
    const h = new Date().getHours();
    if (h < 6) return '늦은 새벽이에요';
    if (h < 12) return '좋은 아침이에요';
    if (h < 18) return '좋은 오후예요';
    return '좋은 저녁이에요';
  })();

  return (
    <div className="space-y-7 lg:space-y-9">

      {/* ──────────── Hero strip — compact greeting ──────────── */}
      <section className="rise rise-1 flex flex-wrap items-end justify-between gap-x-6 gap-y-3" aria-label="대시보드 헤더">
        <div className="min-w-0">
          <p className="eyebrow">{greeting}</p>
          <h2 className="display-1 mt-1">
            {user?.farmname || `${user?.name ?? '농장'}님`}
          </h2>
          <p className="mt-2 max-w-[56ch] text-helper">
            에이전트가 오늘의 농장 데이터를 정리해 두었어요. 우선순위를 확인하세요.
          </p>
        </div>
        <div
          className="inline-flex items-center gap-2 rounded-full border border-[color:var(--color-line)] bg-[color:var(--color-card)] px-3.5 py-2 text-[13px] font-semibold shadow-[var(--shadow-xs)]"
          role="status"
          aria-live="polite"
        >
          {connected && hasData ? (
            <>
              <StatusDot tone="success" pulse />
              <span className="text-[color:var(--color-success)]">실시간</span>
              <span className="text-[color:var(--color-ink-faint)]">·</span>
              <span className="num text-[color:var(--color-ink-soft)]">{formatTime(latest?.timestamp)}</span>
            </>
          ) : (
            <>
              <MdWifiOff aria-hidden className="text-[15px] text-[color:var(--color-ink-mute)]" />
              <span className="text-[color:var(--color-ink-mute)]">오프라인</span>
            </>
          )}
        </div>
      </section>

      {/* ──────────── Field data — sensors + status strip ──────────── */}
      <section className="rise rise-2 space-y-3" aria-labelledby="field-data-title">
        <div className="flex items-end justify-between">
          <h3 id="field-data-title" className="text-[1.125rem] font-bold tracking-[-0.018em] text-[color:var(--color-ink)]">
            현장 데이터
          </h3>
          <Link
            to="/iot"
            className="text-[13px] font-semibold text-[color:var(--color-primary-dark)] transition hover:text-[color:var(--color-primary)]"
          >
            전체 보기 →
          </Link>
        </div>

        <ul className="grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-4">
          {sensorTiles.map(({ label, value, unit, icon: Icon, tintClass, iconClass }) => (
            <li
              key={label}
              className="rounded-2xl border border-[color:var(--color-line)] bg-[color:var(--color-card)] p-4 transition-colors hover:border-[color:var(--color-primary-light)] sm:p-5"
            >
              <div className="flex items-start justify-between">
                <span
                  aria-hidden
                  className={`flex h-10 w-10 items-center justify-center rounded-xl ${tintClass} ${iconClass}`}
                >
                  <Icon className="text-[20px]" />
                </span>
                <span className="text-[12px] font-semibold text-[color:var(--color-ink-faint)]">{label}</span>
              </div>
              <p className="mt-3 num text-[1.75rem] font-bold leading-[1.05] tracking-[-0.022em] text-[color:var(--color-ink)] sm:text-[2rem]">
                {value ?? '--'}
                <span className="ml-1 text-[14px] font-semibold text-[color:var(--color-ink-mute)]">
                  {unit}
                </span>
              </p>
            </li>
          ))}
        </ul>

        <dl className="grid grid-cols-3 divide-x divide-[color:var(--color-line-soft)] overflow-hidden rounded-2xl border border-[color:var(--color-line)] bg-[color:var(--color-card)] text-center">
          <div className="px-4 py-3">
            <dt className="text-[12px] font-semibold uppercase tracking-wide text-[color:var(--color-ink-faint)]">에이전트</dt>
            <dd className="mt-1 text-[15px] font-bold text-[color:var(--color-ink)]">{agentMode}</dd>
          </div>
          <div className="px-4 py-3">
            <dt className="text-[12px] font-semibold uppercase tracking-wide text-[color:var(--color-ink-faint)]">미해결 알림</dt>
            <dd className="mt-1 num text-[15px] font-bold text-[color:var(--color-ink)]">
              {riskCount}
              <span className="ml-0.5 text-[12px] font-semibold text-[color:var(--color-ink-mute)]">건</span>
            </dd>
          </div>
          <div className="px-4 py-3">
            <dt className="text-[12px] font-semibold uppercase tracking-wide text-[color:var(--color-ink-faint)]">관수 이력</dt>
            <dd className="mt-1 num text-[15px] font-bold text-[color:var(--color-ink)]">
              {irrigations.length}
              <span className="ml-0.5 text-[12px] font-semibold text-[color:var(--color-ink-mute)]">건</span>
            </dd>
          </div>
        </dl>
      </section>

      {/* ──────────── Today's briefing — featured AI card ──────────── */}
      <section className="rise rise-3 overflow-hidden rounded-2xl border border-[color:var(--color-line)] bg-[color:var(--color-card)]" aria-labelledby="briefing-title">
        <header className="flex items-center gap-2.5 border-b border-[color:var(--color-line-soft)] bg-[color:var(--color-surface)] px-5 py-3.5 sm:px-6">
          <span aria-hidden className="flex h-8 w-8 items-center justify-center rounded-lg bg-[color:var(--color-primary-soft)] text-[color:var(--color-primary-dark)]">
            <MdAutoAwesome className="text-[16px]" />
          </span>
          <h3 id="briefing-title" className="text-[15px] font-bold text-[color:var(--color-ink)]">오늘의 브리핑</h3>
          {briefing?.cached && (
            <span className="chip text-[11px]" title="캐시된 응답">cached</span>
          )}
          <button
            type="button"
            onClick={() => void fetchBriefing(true)}
            disabled={briefingLoading}
            className="ml-auto rounded-full px-3.5 py-1.5 text-[13px] font-semibold text-[color:var(--color-primary-dark)] transition hover:bg-[color:var(--color-primary-soft)] disabled:opacity-50"
          >
            {briefingLoading ? '분석 중...' : '다시 분석'}
          </button>
        </header>
        <div className="max-h-[24rem] overflow-y-auto px-5 py-5 sm:px-6 sm:py-6">
          {briefingLoading && !briefing ? (
            <Skeleton shape="text" lines={5} label="브리핑 생성 중" />
          ) : briefing ? (
            <div className="text-[15px] leading-[1.75] text-[color:var(--color-ink-soft)]">
              <AgentMarkdown content={briefing.content} />
            </div>
          ) : (
            <EmptyState
              compact
              icon={<MdAutoAwesome className="text-[22px]" />}
              title="브리핑이 아직 준비되지 않았어요"
              description="센서 데이터가 들어오면 자동으로 정리해 드릴게요."
            />
          )}
        </div>
      </section>

      {/* ──────────── Agent actions ──────────── */}
      <section className="rise rise-4" aria-labelledby="agent-actions-title">
        <div className="mb-5">
          <h3 id="agent-actions-title" className="text-[1.375rem] font-bold tracking-[-0.02em] text-[color:var(--color-ink)]">
            에이전트에게 맡기기
          </h3>
          <p className="mt-1.5 text-helper">
            한 번 누르면 사이드 콘솔에서 분석을 시작합니다
          </p>
        </div>

        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {AGENT_ACTIONS.map((action, idx) => (
            <motion.button
              key={action.label}
              type="button"
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.06 + idx * 0.05, duration: 0.28, ease: [0.2, 0.7, 0.2, 1] }}
              onClick={() => void sendAndOpen(action.prompt)}
              className="group relative flex flex-col overflow-hidden rounded-2xl border border-[color:var(--color-line)] bg-[color:var(--color-card)] p-5 text-left transition-all duration-200 hover:-translate-y-1 hover:border-[color:var(--color-primary-light)] hover:shadow-[var(--shadow-md)]"
            >
              <span aria-hidden className="flex h-11 w-11 items-center justify-center rounded-xl bg-[color:var(--color-primary-soft)] text-[color:var(--color-primary-dark)] transition group-hover:bg-[color:var(--color-primary)] group-hover:text-white">
                <action.icon className="text-[22px]" />
              </span>
              <p className="mt-4 text-[16.5px] font-bold leading-[1.3] tracking-[-0.015em] text-[color:var(--color-ink)]">
                {action.label}
              </p>
              <p className="mt-1.5 text-[14px] leading-[1.55] text-[color:var(--color-ink-mute)]">
                {action.detail}
              </p>
              <span className="mt-5 inline-flex items-center gap-1 text-[13px] font-semibold text-[color:var(--color-primary-dark)] transition-transform group-hover:translate-x-0.5">
                위임하기 <MdArrowForward aria-hidden className="text-[15px]" />
              </span>
            </motion.button>
          ))}
        </div>
      </section>

      {/* ──────────── Modules + insights ──────────── */}
      <section className="rise grid gap-7 xl:grid-cols-[minmax(0,1fr)_340px]" aria-labelledby="modules-title">
        <div>
          <div className="mb-5">
            <h3 id="modules-title" className="text-[1.375rem] font-bold tracking-[-0.02em] text-[color:var(--color-ink)]">
              둘러보기
            </h3>
            <p className="mt-1.5 text-helper">
              직접 모듈로 이동하거나 에이전트가 호출할 수 있어요
            </p>
          </div>

          <ul className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {COMMAND_LINKS.map(({ to, label, detail, illust }, index) => (
              <motion.li
                key={to}
                // Last (orphan) card spans 2 cols on xl so 7 items lay as 3+3+(2 wide) — no lonely card
                className={index === COMMAND_LINKS.length - 1 ? 'sm:col-span-2 xl:col-span-2' : ''}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: 0.04 + index * 0.04, duration: 0.25 }}
              >
                <Link
                  to={to}
                  className="group flex h-full items-center gap-4 rounded-2xl border border-[color:var(--color-line)] bg-[color:var(--color-card)] p-3.5 pr-4 transition-all duration-200 hover:-translate-y-0.5 hover:border-[color:var(--color-primary-light)] hover:shadow-[var(--shadow-sm)]"
                >
                  <span
                    aria-hidden
                    className="flex h-16 w-16 flex-shrink-0 items-center justify-center overflow-hidden rounded-xl bg-[color:var(--color-surface)] transition group-hover:bg-[color:var(--color-primary-soft)]"
                  >
                    <img src={illust} alt="" loading="lazy" className="h-full w-full object-cover" />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[16px] font-bold tracking-[-0.012em] text-[color:var(--color-ink)]">
                      {label}
                    </span>
                    <span className="mt-1 block truncate text-[13.5px] text-[color:var(--color-ink-mute)]">
                      {detail}
                    </span>
                  </span>
                  <MdArrowForward
                    aria-hidden
                    className="flex-shrink-0 text-[20px] text-[color:var(--color-ink-faint)] transition group-hover:translate-x-0.5 group-hover:text-[color:var(--color-primary)]"
                  />
                </Link>
              </motion.li>
            ))}
          </ul>
        </div>

        <aside className="space-y-5" aria-label="에이전트 인사이트">
          {/* Recent rulings */}
          <div className="rounded-2xl border border-[color:var(--color-line)] bg-[color:var(--color-card)] p-5">
            <div className="flex items-center gap-2.5">
              <span aria-hidden className="flex h-8 w-8 items-center justify-center rounded-lg bg-[color:var(--color-surface)] text-[color:var(--color-primary)]">
                <MdLockClock className="text-[16px]" />
              </span>
              <h3 className="text-[16px] font-bold tracking-[-0.012em] text-[color:var(--color-ink)]">
                최근 자율 판단
              </h3>
            </div>
            {decisions.length === 0 ? (
              <EmptyState
                compact
                title="기록이 없어요"
                description="자율 판단이 발생하면 여기에 정리됩니다."
                className="mt-3 rounded-xl bg-[color:var(--color-surface)]"
              />
            ) : (
              <ul className="mt-3 space-y-0.5">
                {decisions.slice(0, 4).map((decision) => (
                  <li key={decision.id}>
                    <Link
                      to="/iot"
                      className="block rounded-xl px-3 py-3 transition hover:bg-[color:var(--color-surface)]"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-[14.5px] font-bold text-[color:var(--color-ink)]">
                          {controlLabel(decision.control_type)}
                        </span>
                        <time className="num text-[12.5px] font-semibold text-[color:var(--color-ink-mute)]">
                          {formatTime(decision.timestamp)}
                        </time>
                      </div>
                      <p className="mt-1 line-clamp-2 text-[13.5px] leading-[1.55] text-[color:var(--color-ink-mute)]">
                        {decision.reason}
                      </p>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </div>

          {/* Agent handoff */}
          <div className="relative overflow-hidden rounded-2xl bg-[color:var(--color-primary-dark)] p-5 text-white shadow-[var(--shadow-md)]">
            <div
              aria-hidden
              className="pointer-events-none absolute -bottom-10 -right-10 h-44 w-44 rounded-full bg-[color:var(--color-primary)] opacity-50 blur-3xl"
            />
            <div className="relative">
              <div className="flex items-center gap-2">
                <MdAutoAwesome aria-hidden className="text-[18px] text-[color:var(--color-accent-light)]" />
                <span className="text-[14px] font-semibold tracking-tight opacity-95">에이전트 인계</span>
              </div>
              <p className="mt-3 text-[14.5px] leading-[1.7] opacity-95">
                {latestDecision?.reason ?? '최근 자동 판단이 아직 없습니다.'}
              </p>
              <Link
                to="/iot"
                className="mt-4 inline-flex w-full items-center justify-center gap-2 rounded-xl bg-white/15 px-4 py-3 text-[14px] font-semibold text-white backdrop-blur-sm transition hover:bg-white/25"
              >
                <MdSensors aria-hidden className="text-[17px]" />
                제어 패널 열기
              </Link>
            </div>
          </div>
        </aside>
      </section>
    </div>
  );
}
