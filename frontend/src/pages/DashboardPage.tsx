import { useMemo } from 'react';
import type { ElementType } from 'react';
import { Link } from 'react-router-dom';
import { motion } from 'framer-motion';
import {
  MdAgriculture,
  MdArrowForward,
  MdArticle,
  MdAutoAwesome,
  MdBugReport,
  MdCloud,
  MdLockClock,
  MdPayments,
  MdReviews,
  MdSensors,
  MdShowChart,
  MdTaskAlt,
  MdThermostat,
  MdWaterDrop,
  MdWbSunny,
  MdWifiOff,
} from 'react-icons/md';
import { useAuth } from '@/context/AuthContext';
import { useAIAgent } from '@/hooks/useAIAgent';
import { useSensorData } from '@/hooks/useSensorData';

type Tone = 'emerald' | 'cyan' | 'amber' | 'red' | 'violet' | 'blue' | 'gray';

interface CommandLink {
  to: string;
  label: string;
  detail: string;
  icon: ElementType;
  tone: Tone;
}

const TONE_CLASS: Record<Tone, string> = {
  emerald: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  cyan: 'bg-cyan-50 text-cyan-700 border-cyan-200',
  amber: 'bg-amber-50 text-amber-700 border-amber-200',
  red: 'bg-red-50 text-red-700 border-red-200',
  violet: 'bg-violet-50 text-violet-700 border-violet-200',
  blue: 'bg-blue-50 text-blue-700 border-blue-200',
  gray: 'bg-gray-50 text-gray-600 border-gray-200',
};

const COMMAND_LINKS: CommandLink[] = [
  { to: '/iot', label: '시설 제어', detail: '센서와 자동화 판단', icon: MdSensors, tone: 'cyan' },
  { to: '/diagnosis', label: '진단 워크벤치', detail: '이미지 진단과 처방', icon: MdBugReport, tone: 'red' },
  { to: '/journal', label: '영농 기록', detail: '음성 기록과 통합일지', icon: MdAgriculture, tone: 'emerald' },
  { to: '/weather', label: '기상 작전', detail: '예보 기반 작업 계획', icon: MdCloud, tone: 'blue' },
  { to: '/market', label: '시세 정보', detail: 'KAMIS 가격 변동', icon: MdShowChart, tone: 'amber' },
  { to: '/subsidy', label: '공익직불', detail: '자격과 근거 조항', icon: MdPayments, tone: 'emerald' },
  { to: '/documents', label: '행정 문서', detail: '신고서와 증빙', icon: MdArticle, tone: 'gray' },
  { to: '/reviews', label: '판매 인사이트', detail: '리뷰와 전략', icon: MdReviews, tone: 'violet' },
];

const PRIORITY_ACTIONS = [
  { to: '/iot', label: '제어 이력 검토', icon: MdSensors },
  { to: '/diagnosis', label: '작물 사진 진단', icon: MdBugReport },
  { to: '/journal', label: '오늘 작업 기록', icon: MdAgriculture },
  { to: '/subsidy', label: '직불 리스크 확인', icon: MdPayments },
];

function formatTime(value?: string | null) {
  if (!value) return '수신 없음';
  return new Date(value).toLocaleTimeString('ko-KR', {
    hour: '2-digit',
    minute: '2-digit',
  });
}

function metricValue(value: number | null | undefined, unit: string, digits = 1) {
  if (value == null) return '--';
  return `${value.toFixed(digits)}${unit}`;
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
  const { status, decisions, summary, loading: agentLoading } = useAIAgent();

  const hasData = Boolean(latest);
  const latestDecision = decisions[0] ?? status?.latest_decision ?? null;
  const agentMode = agentLoading
    ? '동기화 중'
    : status?.enabled
      ? '자율 제어'
      : status
        ? '대기'
        : '미연결';

  const riskCount = useMemo(
    () => alerts.filter((alert) => !alert.resolved).length,
    [alerts],
  );

  const sensorTiles = [
    {
      label: '토양 습도',
      value: metricValue(latest?.soilMoisture, '%'),
      icon: MdWaterDrop,
      tone: latest && latest.soilMoisture < 55 ? 'amber' : 'cyan',
    },
    {
      label: '온도',
      value: metricValue(latest?.temperature, '°C'),
      icon: MdThermostat,
      tone: 'red',
    },
    {
      label: '대기 습도',
      value: metricValue(latest?.humidity, '%'),
      icon: MdCloud,
      tone: latest && latest.humidity > 90 ? 'amber' : 'blue',
    },
    {
      label: '조도',
      value: metricValue(latest?.lightIntensity, ' lux', 0),
      icon: MdWbSunny,
      tone: 'amber',
    },
  ] satisfies Array<{
    label: string;
    value: string;
    icon: ElementType;
    tone: Tone;
  }>;

  return (
    <div className="space-y-5">
      <section className="overflow-hidden rounded-lg border border-gray-200 bg-white">
        <div className="grid gap-0 xl:grid-cols-[minmax(0,1fr)_360px]">
          <div className="border-b border-gray-200 p-5 sm:p-6 xl:border-b-0 xl:border-r">
            <div className="mb-5 flex flex-wrap items-start justify-between gap-4">
              <div className="min-w-0">
                <div className="mb-2 inline-flex items-center gap-2 rounded-md border border-primary/20 bg-primary/5 px-2.5 py-1 text-xs font-black text-primary">
                  <MdAutoAwesome className="text-base" />
                  Agentic operations
                </div>
                <h2 className="text-2xl font-black tracking-tight text-gray-950 sm:text-3xl">
                  {user?.farmname || `${user?.name ?? '농장'} 운영 커맨드`}
                </h2>
                <p className="mt-2 max-w-2xl text-sm leading-relaxed text-gray-600">
                  최신 수신 {formatTime(latest?.timestamp)} · 미해결 알림 {riskCount}건 · 관수 이력 {irrigations.length}건
                </p>
              </div>
              <div className="flex items-center gap-2 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2">
                {connected && hasData ? (
                  <span className="h-2.5 w-2.5 rounded-full bg-emerald-500" />
                ) : (
                  <MdWifiOff className="text-lg text-gray-400" />
                )}
                <div className="leading-tight">
                  <p className="text-xs font-bold text-gray-500">IoT</p>
                  <p className="text-sm font-black text-gray-950">
                    {connected && hasData ? formatTime(latest?.timestamp) : '대기'}
                  </p>
                </div>
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              {sensorTiles.map(({ label, value, icon: Icon, tone }) => (
                <div key={label} className={`rounded-lg border p-3 ${TONE_CLASS[tone]}`}>
                  <div className="mb-3 flex items-center justify-between">
                    <Icon className="text-xl" />
                    {!hasData && <span className="text-[11px] font-semibold text-gray-400">offline</span>}
                  </div>
                  <p className="text-xs font-bold opacity-75">{label}</p>
                  <p className="mt-1 text-xl font-black tracking-tight">{hasData ? value : '--'}</p>
                </div>
              ))}
            </div>
          </div>

          <div className="bg-gray-50 p-5 sm:p-6">
            <div className="mb-4 flex items-center justify-between">
              <div>
                <p className="text-xs font-black uppercase tracking-wide text-gray-400">Agent state</p>
                <h3 className="mt-1 text-lg font-black text-gray-950">{agentMode}</h3>
              </div>
              <div
                className={`flex h-11 w-11 items-center justify-center rounded-lg ${
                  status?.enabled ? 'bg-primary text-white' : 'bg-white text-gray-500'
                }`}
              >
                <MdAutoAwesome className="text-2xl" />
              </div>
            </div>

            <div className="space-y-3">
              <div className="rounded-lg border border-gray-200 bg-white p-3">
                <p className="text-xs font-bold text-gray-500">오늘 판단</p>
                <p className="mt-1 text-2xl font-black text-gray-950">
                  {summary?.total ?? status?.total_decisions ?? 0}
                  <span className="ml-1 text-sm font-bold text-gray-400">건</span>
                </p>
              </div>
              <div className="rounded-lg border border-gray-200 bg-white p-3">
                <p className="text-xs font-bold text-gray-500">최근 제어</p>
                <p className="mt-1 font-black text-gray-950">
                  {controlLabel(latestDecision?.control_type)}
                </p>
                <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-gray-500">
                  {latestDecision?.reason ?? '아직 표시할 판단 근거가 없습니다.'}
                </p>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div className="rounded-lg border border-gray-200 bg-white p-3">
                  <p className="text-xs font-bold text-gray-500">미해결 알림</p>
                  <p className="mt-1 text-xl font-black text-gray-950">{riskCount}</p>
                </div>
                <div className="rounded-lg border border-gray-200 bg-white p-3">
                  <p className="text-xs font-bold text-gray-500">관수 이력</p>
                  <p className="mt-1 text-xl font-black text-gray-950">{irrigations.length}</p>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_360px]">
        <div className="space-y-5">
          <div className="rounded-lg border border-gray-200 bg-white p-4">
            <div className="mb-3 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <MdTaskAlt className="text-xl text-primary" />
                <h3 className="text-base font-black text-gray-950">작업 런웨이</h3>
              </div>
              <span className="text-xs font-bold text-gray-400">오늘</span>
            </div>
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              {PRIORITY_ACTIONS.map(({ to, label, icon: Icon }, index) => (
                <motion.div
                  key={to}
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: index * 0.04, duration: 0.18 }}
                >
                  <Link
                    to={to}
                    className="group flex h-full items-center justify-between rounded-lg border border-gray-200 bg-gray-50 px-3 py-3 transition hover:border-primary/30 hover:bg-primary/5"
                  >
                    <span className="flex items-center gap-2 text-sm font-bold text-gray-800">
                      <Icon className="text-xl text-primary" />
                      {label}
                    </span>
                    <MdArrowForward className="text-lg text-gray-400 transition group-hover:text-primary" />
                  </Link>
                </motion.div>
              ))}
            </div>
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
            {COMMAND_LINKS.map(({ to, label, detail, icon: Icon, tone }, index) => (
              <motion.div
                key={to}
                initial={{ opacity: 0, y: 12 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: index * 0.035, duration: 0.18 }}
              >
                <Link
                  to={to}
                  className="group block h-full rounded-lg border border-gray-200 bg-white p-4 transition hover:-translate-y-0.5 hover:border-primary/25 hover:shadow-sm"
                >
                  <div className="mb-4 flex items-start justify-between">
                    <span className={`flex h-10 w-10 items-center justify-center rounded-lg border ${TONE_CLASS[tone]}`}>
                      <Icon className="text-xl" />
                    </span>
                    <MdArrowForward className="text-lg text-gray-300 transition group-hover:text-primary" />
                  </div>
                  <h3 className="text-base font-black text-gray-950">{label}</h3>
                  <p className="mt-1 text-sm leading-relaxed text-gray-500">{detail}</p>
                </Link>
              </motion.div>
            ))}
          </div>
        </div>

        <aside className="space-y-5">
          <div className="rounded-lg border border-gray-200 bg-white p-4">
            <div className="mb-3 flex items-center gap-2">
              <MdLockClock className="text-xl text-primary" />
              <h3 className="text-base font-black text-gray-950">최근 판단 로그</h3>
            </div>
            {decisions.length === 0 ? (
              <p className="rounded-lg bg-gray-50 p-4 text-sm text-gray-400">
                판단 로그를 불러오는 중이거나 기록이 없습니다.
              </p>
            ) : (
              <div className="space-y-2">
                {decisions.slice(0, 4).map((decision) => (
                  <Link
                    key={decision.id}
                    to="/iot"
                    className="block rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 transition hover:border-primary/30 hover:bg-primary/5"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-sm font-black text-gray-900">
                        {controlLabel(decision.control_type)}
                      </span>
                      <span className="text-[11px] font-semibold text-gray-400">
                        {formatTime(decision.timestamp)}
                      </span>
                    </div>
                    <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-gray-500">
                      {decision.reason}
                    </p>
                  </Link>
                ))}
              </div>
            )}
          </div>

          <div className="rounded-lg border border-gray-200 bg-white p-4">
            <div className="mb-3 flex items-center gap-2">
              <MdAutoAwesome className="text-xl text-primary" />
              <h3 className="text-base font-black text-gray-950">Agent handoff</h3>
            </div>
            <div className="space-y-2 text-sm text-gray-600">
              <p className="rounded-lg border border-gray-200 bg-gray-50 p-3">
                {latestDecision?.reason ?? '최근 자동 판단이 아직 없습니다.'}
              </p>
              <Link
                to="/iot"
                className="inline-flex w-full items-center justify-center gap-2 rounded-lg bg-primary px-3 py-2.5 text-sm font-bold text-white transition hover:bg-primary-dark"
              >
                <MdSensors className="text-lg" />
                제어 패널 열기
              </Link>
            </div>
          </div>
        </aside>
      </section>
    </div>
  );
}
