import { useState, useEffect, useMemo, memo, useCallback } from 'react';
import { MdWaterDrop, MdThermostat, MdOpacity, MdWbSunny, MdWarning, MdWifiOff, MdClose, MdAutoAwesome } from 'react-icons/md';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine, BarChart, Bar, Cell } from 'recharts';
import { useSensorData } from '@/hooks/useSensorData';
import { useAIAgent } from '@/hooks/useAIAgent';
import { getCropStageDisplayRanges } from '@/constants/cropProfiles';
import AIAgentPanel from './AIAgentPanel';
import IoTSkeleton from './IoTSkeleton';
import ManualControlPanel from './ManualControlPanel';
import DateRangeFilter, {
  type DateRangeValue,
} from '@/components/DateRangeFilter';

// 날짜 범위로 timestamp 기반 배열 필터. since/until 둘 다 null 이면 원본 반환.
function filterByDateRange<T>(
  items: T[],
  getTimestamp: (item: T) => string,
  since: Date | null,
  until: Date | null,
): T[] {
  if (!since && !until) return items;
  return items.filter((item) => {
    const ts = new Date(getTimestamp(item)).getTime();
    if (since && ts < since.getTime()) return false;
    if (until && ts > until.getTime()) return false;
    return true;
  });
}

function SensorCard({
  icon: Icon, label, value, unit, tintClass, iconClass,
  threshold, warning, disabled, optimalRange, optimalLabel,
}: {
  icon: React.ElementType; label: string; value: number | null; unit: string;
  tintClass: string; iconClass: string;
  threshold?: number; warning?: boolean; disabled?: boolean;
  /** AI Agent 작물 프로필 권장 범위 [low, high]. 표시 + warning 계산에 사용. */
  optimalRange?: [number, number];
  /** optimalRange 라벨 (예: "토마토 / 영양생장기"). */
  optimalLabel?: string;
}) {
  return (
    <div
      className={`rounded-2xl border bg-[color:var(--color-card)] p-5 transition sm:p-6 ${
        disabled
          ? 'border-[color:var(--color-line-soft)] opacity-60'
          : warning
            ? 'border-[color:var(--color-accent)]/45 bg-[#FBF5E5]'
            : 'border-[color:var(--color-line)]'
      }`}
    >
      <div className="flex items-center justify-between">
        <span
          aria-hidden
          className={`flex h-11 w-11 items-center justify-center rounded-xl sm:h-12 sm:w-12 ${
            disabled ? 'tint-neutral text-[color:var(--color-ink-faint)]' : `${tintClass} ${iconClass}`
          }`}
        >
          <Icon className="text-[22px] sm:text-[24px]" />
        </span>
        {!disabled && warning && (
          <span className="inline-flex items-center gap-1 text-[12.5px] font-bold text-[color:var(--color-accent-dark)]">
            <MdWarning aria-hidden className="text-base" /> 주의
          </span>
        )}
        {disabled && (
          <span className="inline-flex items-center gap-1 text-[12.5px] font-semibold text-[color:var(--color-ink-faint)]">
            <MdWifiOff aria-hidden className="text-base" /> 비활성
          </span>
        )}
      </div>
      <p className={`mt-4 text-[14px] font-semibold ${disabled ? 'text-[color:var(--color-ink-faint)]' : 'text-[color:var(--color-ink-mute)]'}`}>
        {label}
      </p>
      <p className={`mt-1 num text-[1.875rem] font-bold leading-[1.05] tracking-[-0.025em] sm:text-[2.25rem] ${disabled ? 'text-[color:var(--color-ink-faint)]' : 'text-[color:var(--color-ink)]'}`}>
        {value !== null ? value.toFixed(1) : '--.-'}
        <span className="ml-1 text-[15px] font-semibold text-[color:var(--color-ink-mute)] sm:text-[17px]">{unit}</span>
      </p>
      {/* progress bar — threshold(soil) 또는 optimalRange 가 있으면 표시. */}
      {(threshold || optimalRange) && !disabled && value !== null && (
        <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-[color:var(--color-surface-deep)]">
          <div
            className={`h-full rounded-full transition-all ${
              optimalRange
                ? value < optimalRange[0] || value > optimalRange[1]
                  ? 'bg-[color:var(--color-warning)]'
                  : 'bg-[color:var(--color-success)]'
                : threshold && value < threshold
                  ? 'bg-[color:var(--color-warning)]'
                  : 'bg-[color:var(--color-success)]'
            }`}
            style={{
              // unit 이 lux 면 0~100k 스케일을 0~100% 로 환산. 그 외는 0~100 그대로.
              width: `${Math.min(
                100,
                unit.trim() === 'lux' ? (value / 100000) * 100 : value,
              )}%`,
            }}
          />
        </div>
      )}
      {(threshold || optimalRange) && disabled && (
        <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-[color:var(--color-surface-deep)]" />
      )}

      {/* 적정 범위 라인 — optimalRange 우선, 없으면 단일 임계치(기준) 표시. */}
      {(optimalRange || threshold) && (
        <div className={`mt-2 flex items-center gap-1.5 text-[11.5px] ${disabled ? 'text-[color:var(--color-ink-faint)]' : 'text-[color:var(--color-primary-dark)]'}`}>
          <MdAutoAwesome aria-hidden className={`text-[13px] ${disabled ? '' : 'text-[color:var(--color-primary)]'}`} />
          {optimalRange ? (
            <span className="font-semibold">
              적정 <span className="num">{formatRangeNumber(optimalRange[0])}~{formatRangeNumber(optimalRange[1])}</span>{unit}
            </span>
          ) : (
            <span className="font-semibold">
              기준 <span className="num">{threshold}</span>{unit}
            </span>
          )}
          {optimalLabel && (
            <span className="ml-auto truncate text-[color:var(--color-ink-faint)]" title={optimalLabel}>
              {optimalLabel}
            </span>
          )}
        </div>
      )}
    </div>
  );
}

/** 천단위 콤마 포맷. lux 등 큰 수 가독성용. */
function formatRangeNumber(n: number): string {
  return n >= 1000 ? n.toLocaleString('ko-KR') : String(n);
}

type ChartData = { time: string; soilMoisture: number; temperature: number; humidity: number }[];
const IoTCharts = memo(function IoTCharts({ chartData }: { chartData: ChartData }) {
  const [mounted, setMounted] = useState(false);
  useEffect(() => { setMounted(true); }, []);

  if (chartData.length === 0) {
    return (
      <div className="card !p-8 text-center text-[color:var(--color-ink-faint)]">
        <p className="text-lg">센서 데이터가 아직 없습니다</p>
        <p className="text-sm mt-1">ESP8266에서 데이터를 전송하면 차트가 표시됩니다</p>
      </div>
    );
  }

  return (
    <>
      <div className="card">
        <h3 className="section-title mb-4">토양 습도 추이</h3>
        {mounted && (
          <div className="h-[200px] sm:h-[280px] overflow-hidden">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chartData}>
                <CartesianGrid strokeDasharray="3 3" stroke="#E5E7EB" />
                <XAxis dataKey="time" tick={{ fontSize: 12 }} />
                <YAxis domain={[0, 100]} tick={{ fontSize: 12 }} />
                <Tooltip />
                <ReferenceLine y={55} stroke="#EAB308" strokeDasharray="5 5" label="임계값 55%" />
                <Line type="monotone" dataKey="soilMoisture" stroke="#3B82F6" strokeWidth={2} dot={false} name="토양 습도 (%)" />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>

      <div className="card">
        <h3 className="section-title mb-4">온도 · 습도 추이</h3>
        {mounted && (
          <div className="h-[200px] sm:h-[250px] overflow-hidden">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chartData}>
                <CartesianGrid strokeDasharray="3 3" stroke="#E5E7EB" />
                <XAxis dataKey="time" tick={{ fontSize: 12 }} />
                <YAxis tick={{ fontSize: 12 }} />
                <Tooltip />
                <Line type="monotone" dataKey="temperature" stroke="#EF4444" strokeWidth={2} dot={false} name="온도 (°C)" />
                <Line type="monotone" dataKey="humidity" stroke="#14B8A6" strokeWidth={2} dot={false} name="습도 (%)" />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>
    </>
  );
});

interface IrrigationEvent {
  id: string;
  triggeredAt: string;
  reason: string;
  valveAction: '열림' | '닫힘';
  duration: number;
  autoTriggered: boolean;
}

interface IrrigationModalProps {
  irrigations: IrrigationEvent[];
  onClose: () => void;
}

function IrrigationModal({ irrigations, onClose }: IrrigationModalProps) {
  const [mounted, setMounted] = useState(false);
  useEffect(() => { setMounted(true); }, []);

  // ESC 키로 닫기
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  // 모달 열릴 때 스크롤 방지
  useEffect(() => {
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = ''; };
  }, []);

  const chartData = irrigations.map((e) => ({
    label: new Date(e.triggeredAt).toLocaleString('ko-KR', {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    }),
    duration: e.duration > 0 ? e.duration : 1,
    valveAction: e.valveAction,
    reason: e.reason,
  }));

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ backgroundColor: 'rgba(0,0,0,0.5)' }}
      onClick={onClose}
    >
      <div
        className="bg-white rounded-2xl shadow-2xl w-full max-w-2xl max-h-[90vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* 헤더 */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-[color:var(--color-line-soft)]">
          <h2 className="text-lg font-bold text-[color:var(--color-ink)]">관수 이력 상세</h2>
          <button
            onClick={onClose}
            className="w-8 h-8 flex items-center justify-center rounded-lg text-[color:var(--color-ink-faint)] hover:text-[color:var(--color-ink-soft)] hover:bg-[color:var(--color-surface-deep)] transition-colors"
            aria-label="닫기"
          >
            <MdClose className="text-xl" />
          </button>
        </div>

        {/* 그래프 영역 */}
        <div className="px-6 pt-5 pb-2">
          <p className="text-sm font-medium text-[color:var(--color-ink-mute)] mb-3">관수 지속 시간 (분)</p>
          {mounted && chartData.length > 0 ? (
            <div className="h-[200px]">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={chartData} margin={{ top: 4, right: 8, left: -20, bottom: 40 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#E5E7EB" />
                  <XAxis
                    dataKey="label"
                    tick={{ fontSize: 10, fill: '#9CA3AF' }}
                    angle={-35}
                    textAnchor="end"
                    interval={0}
                  />
                  <YAxis tick={{ fontSize: 11, fill: '#9CA3AF' }} allowDecimals={false} />
                  <Tooltip
                    formatter={(value, _name, item) => {
                      // Recharts v3: value 는 ValueType | undefined, item.payload 에서 valveAction 접근.
                      if (value == null) return ['-', ''];
                      const valveAction = item?.payload?.valveAction ?? '-';
                      return [`${value}분`, `밸브 ${valveAction}`];
                    }}
                    labelFormatter={(label) => String(label ?? '')}
                    contentStyle={{ fontSize: 12, borderRadius: 8 }}
                  />
                  <Bar dataKey="duration" radius={[4, 4, 0, 0]} maxBarSize={40}>
                    {chartData.map((entry, index) => (
                      <Cell
                        key={index}
                        fill={entry.valveAction === '열림' ? '#3B82F6' : '#9CA3AF'}
                      />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <div className="h-[200px] flex items-center justify-center text-[color:var(--color-ink-disabled)] text-sm">
              데이터 없음
            </div>
          )}
          <div className="flex items-center gap-4 mt-1 text-xs text-[color:var(--color-ink-mute)]">
            <span className="flex items-center gap-1.5">
              <span className="w-3 h-3 rounded-sm bg-blue-500 inline-block" />
              밸브 열림
            </span>
            <span className="flex items-center gap-1.5">
              <span className="w-3 h-3 rounded-sm bg-gray-400 inline-block" />
              밸브 닫힘
            </span>
          </div>
        </div>

        {/* 리스트 영역 (스크롤) */}
        <div className="px-6 pb-6 overflow-y-auto flex-1 mt-2">
          <p className="text-sm font-medium text-[color:var(--color-ink-mute)] mb-2">전체 이력 ({irrigations.length}건)</p>
          <div className="space-y-2">
            {irrigations.map((e) => (
              <div key={e.id} className="flex items-center gap-3 p-3 rounded-xl bg-[color:var(--color-surface)]">
                <span className={`w-3 h-3 rounded-full flex-shrink-0 ${e.valveAction === '열림' ? 'bg-blue-500' : 'bg-gray-400'}`} />
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-[color:var(--color-ink)] truncate">{e.reason}</p>
                  <p className="text-xs text-[color:var(--color-ink-faint)]">
                    {new Date(e.triggeredAt).toLocaleString('ko-KR', {
                      month: 'short',
                      day: 'numeric',
                      hour: '2-digit',
                      minute: '2-digit',
                    })}
                    {e.duration > 0 && ` · ${e.duration}분`}
                    {e.autoTriggered && <span className="ml-1 text-[#2D5F2D]">· 자동</span>}
                  </p>
                </div>
                <span className={`badge text-xs flex-shrink-0 ${e.valveAction === '열림' ? 'badge-info' : 'badge-success'}`}>
                  밸브 {e.valveAction}
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

const INITIAL_COUNT = 3;

interface SensorAlertItem {
  id: string;
  timestamp: string;
  severity: string;
  message: string;
  resolved?: boolean;
  /** 같은 (type, severity) 알림이 같은 시간 버킷 내에 몇 건 있었는지. throttle on 일 때만 set. */
  groupedCount?: number;
}

// 알림 묶기 간격 옵션 — 같은 종류/심각도 알림을 N분 단위로 묶어 1건으로 표시.
type ThrottleMin = 0 | 5 | 15 | 30 | 60 | 180;
const ALERT_THROTTLE_OPTIONS: { value: ThrottleMin; label: string }[] = [
  { value: 0, label: '끔' },
  { value: 5, label: '5분' },
  { value: 15, label: '15분' },
  { value: 30, label: '30분' },
  { value: 60, label: '1시간' },
  { value: 180, label: '3시간' },
];

function AlertsModal({
  alerts,
  onClose,
}: {
  alerts: SensorAlertItem[];
  onClose: () => void;
}) {
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  useEffect(() => {
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = '';
    };
  }, []);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ backgroundColor: 'rgba(0,0,0,0.5)' }}
      onClick={onClose}
    >
      <div
        className="bg-white rounded-2xl shadow-2xl w-full max-w-2xl max-h-[90vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-6 py-4 border-b border-[color:var(--color-line-soft)]">
          <h2 className="text-lg font-bold text-[color:var(--color-ink)]">센서 알림 상세</h2>
          <button
            onClick={onClose}
            className="w-8 h-8 flex items-center justify-center rounded-lg text-[color:var(--color-ink-faint)] hover:text-[color:var(--color-ink-soft)] hover:bg-[color:var(--color-surface-deep)] transition-colors"
            aria-label="닫기"
          >
            <MdClose className="text-xl" />
          </button>
        </div>
        <div className="px-6 py-4 overflow-y-auto flex-1">
          <p className="text-sm font-medium text-[color:var(--color-ink-mute)] mb-2">
            전체 알림 ({alerts.length}건)
          </p>
          {alerts.length === 0 ? (
            <p className="text-center text-[color:var(--color-ink-faint)] text-sm py-8">
              해당 기간에 알림이 없습니다
            </p>
          ) : (
            <div className="space-y-2">
              {alerts.map((a) => (
                <div
                  key={a.id}
                  className={`flex items-center gap-3 p-3 rounded-xl ${
                    a.severity === '위험' || a.severity === '경고'
                      ? 'bg-[color:var(--color-danger-light)]'
                      : a.severity === '주의'
                      ? 'bg-[color:var(--tint-warning)]'
                      : 'bg-[color:var(--tint-info)]'
                  }`}
                >
                  <span
                    className={`badge text-xs flex-shrink-0 ${
                      a.severity === '위험' || a.severity === '경고'
                        ? 'badge-danger'
                        : a.severity === '주의'
                        ? 'badge-warning'
                        : 'badge-info'
                    }`}
                  >
                    {a.severity}
                  </span>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm text-[color:var(--color-ink)]">
                      {a.message}
                      {a.groupedCount && a.groupedCount > 1 && (
                        <span className="ml-2 inline-flex items-center rounded-full bg-[color:var(--color-surface-deep)] px-2 py-0.5 text-[11px] font-semibold text-[color:var(--color-ink-mute)]">
                          외 {a.groupedCount - 1}건
                        </span>
                      )}
                    </p>
                    <p className="text-xs text-[color:var(--color-ink-faint)]">
                      {new Date(a.timestamp).toLocaleString('ko-KR', {
                        month: 'short',
                        day: 'numeric',
                        hour: '2-digit',
                        minute: '2-digit',
                      })}
                    </p>
                  </div>
                  {a.resolved && (
                    <span className="text-xs text-[color:var(--color-primary)] flex-shrink-0">해결됨</span>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default function IoTDashboardPage() {
  const { latest, history, alerts, irrigations, connected, loading } = useSensorData();
  // AI Agent 상태에서 활성 작물 프로필을 끌어와 센서카드 임계 표시에 반영.
  // status 가 null (Relay 미연결) 이어도 센서카드는 보이게 — optionally
  // optimalRange 가 undefined 이면 기존 표시만 유지된다.
  const { status: agentStatus } = useAIAgent();
  const cropProfile = agentStatus?.crop_profile ?? null;
  const optimalLabel = cropProfile ? `${cropProfile.name} / ${cropProfile.growth_stage}` : undefined;
  const tempRange = cropProfile?.optimal_temp;
  const humidityRange = cropProfile?.optimal_humidity;
  // 토양 습도 / 조도 는 백엔드 CropProfile 에 없으므로, 작물명+단계 로 프론트엔드 표시용
  // 프리셋(constants/cropProfiles.ts) 에서 끌어온다. 매칭 안되면 null → 표시 생략.
  const displayRanges = cropProfile
    ? getCropStageDisplayRanges(cropProfile.name, cropProfile.growth_stage)
    : null;
  const soilRange = displayRanges?.optimal_soil_moisture;
  const luxRange = displayRanges?.optimal_light_lux;
  const hasData = !!latest;
  const inactive = !connected || !hasData;

  const [irrigationModalOpen, setIrrigationModalOpen] = useState(false);
  const [alertsModalOpen, setAlertsModalOpen] = useState(false);

  const handleOpenIrrigationModal = useCallback(() => setIrrigationModalOpen(true), []);
  const handleCloseIrrigationModal = useCallback(() => setIrrigationModalOpen(false), []);
  const handleOpenAlertsModal = useCallback(() => setAlertsModalOpen(true), []);
  const handleCloseAlertsModal = useCallback(() => setAlertsModalOpen(false), []);

  // 관수 이력 · 센서 알림 날짜 범위 필터 (클라이언트 사이드)
  const [irrigationRange, setIrrigationRange] = useState<DateRangeValue>({
    since: null,
    until: null,
    preset: 'all',
  });
  const [alertsRange, setAlertsRange] = useState<DateRangeValue>({
    since: null,
    until: null,
    preset: 'all',
  });
  // 센서 알림 묶기 간격 (분). 0 이면 묶기 끔(원본 그대로 표시).
  const [alertsThrottleMin, setAlertsThrottleMin] = useState<ThrottleMin>(0);

  // 관수 이력 표시 정책:
  //   - 실제 관수가 발생한 시점만 보여준다 (밸브 "열림" 이벤트 + duration > 0).
  //   - 백엔드(IoT relay) 가 상태폴링/닫힘/heartbeat 등을 같은 채널로 흘려 보내
  //     이력이 누적되는 현상을 프론트에서 차단.
  //   - id 단위 dedup 은 useSensorData 에서 이미 수행하므로 여기선 의미적 필터만.
  const filteredIrrigations = useMemo(
    () =>
      filterByDateRange(
        irrigations.filter((e) => e.valveAction === '열림' && e.duration > 0),
        (e) => e.triggeredAt,
        irrigationRange.since,
        irrigationRange.until,
      ),
    [irrigations, irrigationRange.since, irrigationRange.until],
  );

  const filteredAlerts = useMemo<SensorAlertItem[]>(() => {
    const ranged = filterByDateRange(
      alerts,
      (a) => a.timestamp,
      alertsRange.since,
      alertsRange.until,
    );

    // throttle 끔 → 그대로 (groupedCount 미사용)
    if (alertsThrottleMin === 0) return ranged;

    // throttle on → (type, severity, bucket) 단위로 그룹핑 후 가장 최근 1건 + count.
    // bucket 은 floor(timestamp / intervalMs) — 동일 윈도우 내 알림이 같은 키를 공유.
    const intervalMs = alertsThrottleMin * 60 * 1000;
    const map = new Map<string, { repr: typeof ranged[number]; count: number }>();
    for (const a of ranged) {
      const ts = new Date(a.timestamp).getTime();
      const bucket = Math.floor(ts / intervalMs);
      const key = `${a.type}|${a.severity}|${bucket}`;
      const existing = map.get(key);
      if (!existing) {
        map.set(key, { repr: a, count: 1 });
      } else {
        existing.count += 1;
        if (ts > new Date(existing.repr.timestamp).getTime()) existing.repr = a;
      }
    }
    return Array.from(map.values())
      .map(({ repr, count }) => ({ ...repr, groupedCount: count }))
      .sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime());
  }, [alerts, alertsRange.since, alertsRange.until, alertsThrottleMin]);

  const chartData = useMemo(() =>
    history.map(r => ({
      time: new Date(r.timestamp).toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' }),
      soilMoisture: r.soilMoisture,
      temperature: r.temperature,
      humidity: r.humidity,
    })),
  [history]);

  // iter-22 — 첫 fetchAll 응답 전엔 shape skeleton 으로 "연결 시도 중" 을 명시적으로
  // 표시. 응답 후엔 (성공이든 실패든) 기존 분기(연결됨/대기/끊김)가 의미 있는 상태를
  // 보여준다. iter-15/17/19/20/21 과 동일한 패턴.
  if (loading && !latest && !connected) {
    return <IoTSkeleton />;
  }

  return (
    <div className="space-y-6">
      {/* Connection status */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-[13.5px]">
        {connected && hasData ? (
          <>
            <span className="live-dot" />
            <span className="font-bold text-[color:var(--color-success)]">연결됨</span>
            <span className="text-[color:var(--color-ink-mute)]">
              마지막 수신 · {new Date(latest!.timestamp).toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
            </span>
          </>
        ) : connected && !hasData ? (
          <>
            <span className="relative flex h-2 w-2">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-[color:var(--color-accent)] opacity-75" />
              <span className="relative inline-flex h-2 w-2 rounded-full bg-[color:var(--color-accent)]" />
            </span>
            <span className="font-bold text-[color:var(--color-accent-dark)]">서버 연결됨 · 센서 데이터 대기 중</span>
          </>
        ) : (
          <>
            <MdWifiOff className="text-[18px] text-[color:var(--color-ink-mute)]" />
            <span className="font-bold text-[color:var(--color-ink-mute)]">백엔드 연결 안 됨</span>
            <span className="text-[12px] text-[color:var(--color-ink-faint)]">백엔드 응답 없음</span>
          </>
        )}
      </div>

      {/* Sensor Cards — always visible, disabled when no data */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <SensorCard
          icon={MdWaterDrop} label="토양 습도"
          value={hasData ? latest!.soilMoisture : null} unit="%"
          tintClass="tint-info" iconClass="text-[color:var(--color-info)]"
          // 작물 프로필이 있으면 해당 범위, 없으면 폴백 임계 55%.
          threshold={soilRange ? undefined : 55}
          optimalRange={soilRange}
          optimalLabel={soilRange ? optimalLabel : undefined}
          warning={
            hasData && soilRange
              ? latest!.soilMoisture < soilRange[0] || latest!.soilMoisture > soilRange[1]
              : hasData && latest!.soilMoisture < 55
          }
          disabled={inactive}
        />
        <SensorCard
          icon={MdThermostat} label="온도"
          value={hasData ? latest!.temperature : null} unit="°C"
          tintClass="tint-danger" iconClass="text-[color:var(--color-danger)]"
          // AI Agent 권장 온도 범위 밖으로 벗어나면 warning. 프로필이 없으면 표시만 비움.
          warning={
            hasData && tempRange
              ? latest!.temperature < tempRange[0] || latest!.temperature > tempRange[1]
              : undefined
          }
          optimalRange={tempRange}
          optimalLabel={optimalLabel}
          disabled={inactive}
        />
        <SensorCard
          icon={MdOpacity} label="대기 습도"
          value={hasData ? latest!.humidity : null} unit="%"
          tintClass="tint-success" iconClass="text-[color:var(--color-primary)]"
          warning={
            hasData && humidityRange
              ? latest!.humidity < humidityRange[0] || latest!.humidity > humidityRange[1]
              : hasData && latest!.humidity > 90
          }
          optimalRange={humidityRange}
          optimalLabel={optimalLabel}
          disabled={inactive}
        />
        <SensorCard
          icon={MdWbSunny} label="조도"
          value={hasData ? latest!.lightIntensity : null} unit=" lux"
          tintClass="tint-warning" iconClass="text-[color:var(--color-accent-dark)]"
          warning={
            hasData && luxRange
              ? latest!.lightIntensity < luxRange[0] || latest!.lightIntensity > luxRange[1]
              : undefined
          }
          optimalRange={luxRange}
          optimalLabel={luxRange ? optimalLabel : undefined}
          disabled={inactive}
        />
      </div>

      <IoTCharts chartData={chartData} />

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Irrigation Events */}
        <div className={`card ${inactive ? 'opacity-50' : ''}`}>
          <div className="flex items-center justify-between gap-2 mb-3 flex-wrap">
            <h3 className="section-title !mb-0">관수 이력</h3>
            <DateRangeFilter
              value={irrigationRange}
              onChange={setIrrigationRange}
            />
          </div>
          {filteredIrrigations.length === 0 ? (
            <p className="text-[color:var(--color-ink-faint)] text-sm text-center py-4">
              {irrigations.length === 0
                ? '관수 이력이 없습니다'
                : '해당 기간에 관수 이력이 없습니다'}
            </p>
          ) : (
            <>
              <div className="space-y-2">
                {filteredIrrigations.slice(0, INITIAL_COUNT).map((e) => (
                  <div key={e.id} className="flex items-center gap-3 p-3 rounded-xl bg-[color:var(--color-surface)]">
                    <span className={`w-3 h-3 rounded-full ${e.valveAction === '열림' ? 'bg-blue-500' : 'bg-gray-400'}`} />
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-[color:var(--color-ink)] truncate">{e.reason}</p>
                      <p className="text-xs text-[color:var(--color-ink-faint)]">
                        {new Date(e.triggeredAt).toLocaleString('ko-KR', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
                        {e.duration > 0 && ` · ${e.duration}분`}
                      </p>
                    </div>
                    <span className={`badge text-xs ${e.valveAction === '열림' ? 'badge-info' : 'badge-success'}`}>
                      밸브 {e.valveAction}
                    </span>
                  </div>
                ))}
              </div>
              {filteredIrrigations.length > INITIAL_COUNT && (
                <div className="flex gap-2 mt-3">
                  <button
                    onClick={handleOpenIrrigationModal}
                    className="text-[12.5px] font-semibold px-3.5 py-2 rounded-full border border-[color:var(--color-line)] text-[color:var(--color-primary-dark)] hover:bg-[color:var(--color-primary-soft)] hover:border-[color:var(--color-primary)] transition-colors"
                  >
                    더보기 ({filteredIrrigations.length - INITIAL_COUNT}건 더)
                  </button>
                </div>
              )}
            </>
          )}
        </div>

        {/* Alerts */}
        <div className={`card ${inactive ? 'opacity-50' : ''}`}>
          <div className="flex items-center justify-between gap-2 mb-3 flex-wrap">
            <h3 className="section-title !mb-0">센서 알림</h3>
            <div className="flex items-center gap-2 flex-wrap">
              <label className="flex items-center gap-1.5 text-[12.5px] text-[color:var(--color-ink-mute)]">
                <span>묶기</span>
                <select
                  value={alertsThrottleMin}
                  onChange={(e) => setAlertsThrottleMin(Number(e.target.value) as ThrottleMin)}
                  className="rounded-md border border-[color:var(--color-line)] bg-white px-2 py-1 text-[12.5px] text-[color:var(--color-ink-soft)] focus:outline-none focus:ring-2 focus:ring-[color:var(--color-primary)]/30 focus:border-[color:var(--color-primary)]"
                  aria-label="알림 묶기 간격"
                >
                  {ALERT_THROTTLE_OPTIONS.map((o) => (
                    <option key={o.value} value={o.value}>{o.label}</option>
                  ))}
                </select>
              </label>
              <DateRangeFilter value={alertsRange} onChange={setAlertsRange} />
            </div>
          </div>
          {filteredAlerts.length === 0 ? (
            <p className="text-[color:var(--color-ink-faint)] text-sm text-center py-4">
              {alerts.length === 0
                ? '알림이 없습니다'
                : '해당 기간에 알림이 없습니다'}
            </p>
          ) : (
            <>
              <div className="space-y-2">
                {filteredAlerts.slice(0, INITIAL_COUNT).map((a) => (
                  <div key={a.id} className={`flex items-center gap-3 p-3 rounded-xl ${
                    a.severity === '위험' || a.severity === '경고' ? 'bg-[color:var(--color-danger-light)]' :
                    a.severity === '주의' ? 'bg-[color:var(--tint-warning)]' : 'bg-[color:var(--tint-info)]'
                  }`}>
                    <span className={`badge text-xs ${
                      a.severity === '위험' || a.severity === '경고' ? 'badge-danger' :
                      a.severity === '주의' ? 'badge-warning' : 'badge-info'
                    }`}>
                      {a.severity}
                    </span>
                    <div className="flex-1 min-w-0">
                      <p className="text-sm text-[color:var(--color-ink)]">
                        {a.message}
                        {a.groupedCount && a.groupedCount > 1 && (
                          <span className="ml-2 inline-flex items-center rounded-full bg-[color:var(--color-surface-deep)] px-2 py-0.5 text-[11px] font-semibold text-[color:var(--color-ink-mute)]">
                            외 {a.groupedCount - 1}건
                          </span>
                        )}
                      </p>
                      <p className="text-xs text-[color:var(--color-ink-faint)]">
                        {new Date(a.timestamp).toLocaleString('ko-KR', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
                      </p>
                    </div>
                    {a.resolved && <span className="text-xs text-[color:var(--color-primary)]">해결됨</span>}
                  </div>
                ))}
              </div>
              {filteredAlerts.length > INITIAL_COUNT && (
                <div className="flex gap-2 mt-3">
                  <button
                    onClick={handleOpenAlertsModal}
                    className="text-[12.5px] font-semibold px-3.5 py-2 rounded-full border border-[color:var(--color-line)] text-[color:var(--color-primary-dark)] hover:bg-[color:var(--color-primary-soft)] hover:border-[color:var(--color-primary)] transition-colors"
                  >
                    더보기 ({filteredAlerts.length - INITIAL_COUNT}건 더)
                  </button>
                </div>
              )}
            </>
          )}
        </div>
      </div>

      {/* 수동 제어 패널 */}
      <ManualControlPanel />

      {/* AI Agent 제어 패널 */}
      <AIAgentPanel />

      {irrigationModalOpen && (
        <IrrigationModal
          irrigations={filteredIrrigations}
          onClose={handleCloseIrrigationModal}
        />
      )}
      {alertsModalOpen && (
        <AlertsModal
          alerts={filteredAlerts}
          onClose={handleCloseAlertsModal}
        />
      )}
    </div>
  );
}
