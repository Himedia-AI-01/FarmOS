import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  MdAccessTime,
  MdBlock,
  MdCalendarMonth,
  MdCheckCircle,
  MdCloud,
  MdRefresh,
  MdWarning,
} from 'react-icons/md';
import { FARMOS_API_BASE } from '@/lib/api';
import WeatherSkeleton from './WeatherSkeleton';

interface CurrentWeather {
  temperature?: number;
  humidity?: number;
  wind_speed?: number;
  precipitation?: number;
  precipitation_type?: string;
  observed_at?: string;
  base_date?: string;
  base_time?: string;
}

interface ForecastItem {
  hours_ahead?: number;
  date?: string;
  time?: string;
  valid_at?: string;
  temperature?: number;
  humidity?: number;
  wind_speed?: number;
  sky?: string;
  precipitation_prob?: number;
  precipitation?: number;
}

interface DailyForecast {
  date: string;
  weekday?: string;
  day_offset?: number;
  temp_min?: number | null;
  temp_max?: number | null;
  humidity_avg?: number | null;
  wind_speed_max?: number | null;
  precipitation_prob?: number | null;
  precipitation?: number | null;
  sky?: string;
}

interface WeatherPayload {
  current?: CurrentWeather;
  forecasts?: ForecastItem[];
  daily_forecasts?: DailyForecast[];
  source?: 'kma' | 'mock';
  timezone?: string;
  generated_at?: string;
  nx?: number;
  ny?: number;
}

const formatDateTime = (value?: string) => {
  if (!value) return '확인 중';
  return new Date(value).toLocaleString('ko-KR', {
    timeZone: 'Asia/Seoul',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
};

// generated_at(서버 응답 생성 시각) 또는 마지막 새로고침 시각을 상대 시간으로 표현.
// 60초 미만: "방금 전", 60분 미만: "N분 전", 24시간 미만: "N시간 전", 그 외엔 빈 문자열.
const formatRelativeTime = (value: string | number | undefined, now: number): string => {
  if (value == null) return '';
  const ts = typeof value === 'number' ? value : new Date(value).getTime();
  if (!Number.isFinite(ts)) return '';
  const diff = Math.max(0, now - ts);
  if (diff < 60_000) return '방금 전';
  if (diff < 60 * 60_000) return `${Math.floor(diff / 60_000)}분 전`;
  if (diff < 24 * 60 * 60_000) return `${Math.floor(diff / (60 * 60_000))}시간 전`;
  return '';
};

const formatDay = (item: ForecastItem) => {
  if (item.valid_at) {
    return new Date(item.valid_at).toLocaleDateString('ko-KR', {
      timeZone: 'Asia/Seoul',
      weekday: 'short',
      month: 'short',
      day: 'numeric',
    });
  }
  return item.date ?? '예보';
};

const formatTime = (item: ForecastItem) => {
  if (item.valid_at) {
    return new Date(item.valid_at).toLocaleTimeString('ko-KR', {
      timeZone: 'Asia/Seoul',
      hour: '2-digit',
      minute: '2-digit',
    });
  }
  return item.time ?? `${item.hours_ahead ?? '?'}시간 후`;
};

const isRainy = (item: ForecastItem) =>
  (item.precipitation ?? 0) > 0 || ['비', '비/눈', '눈', '빗방울', '진눈깨비'].includes(item.sky ?? '');

const weatherTone = (item: ForecastItem) => {
  if (isRainy(item)) return 'border-[color:var(--color-info)]/30 bg-[color:var(--tint-info)]';
  if ((item.wind_speed ?? 0) >= 7) return 'border-amber-200 bg-[color:var(--tint-warning)]';
  if (item.sky === '맑음') return 'border-[color:var(--color-primary-soft)] bg-[color:var(--color-primary-soft)]';
  return 'border-[color:var(--color-line-soft)] bg-[color:var(--color-surface)]';
};

// 일일 예보 카드 톤 — sky 텍스트 또는 부분 매칭(예: "흐리고 비")으로 판정
const dailyTone = (item: DailyForecast) => {
  const sky = item.sky ?? '';
  if (
    (item.precipitation ?? 0) > 0 ||
    /비|눈|빗방울|진눈깨비|소나기/.test(sky)
  ) {
    return 'border-[color:var(--color-info)]/30 bg-[color:var(--tint-info)]';
  }
  if ((item.precipitation_prob ?? 0) >= 60) return 'border-[color:var(--color-info)]/20 bg-[color:var(--tint-info)]/60';
  if ((item.wind_speed_max ?? 0) >= 7) return 'border-amber-200 bg-[color:var(--tint-warning)]';
  if (sky === '맑음') return 'border-[color:var(--color-primary-soft)] bg-[color:var(--color-primary-soft)]';
  if (/흐림/.test(sky)) return 'border-[color:var(--color-line)] bg-[color:var(--color-surface-deep)]';
  return 'border-[color:var(--color-line-soft)] bg-[color:var(--color-surface)]';
};

const dailySkyEmoji = (item: DailyForecast) => {
  const sky = item.sky ?? '';
  if (/눈/.test(sky)) return '❄️';
  if (/비|소나기|빗방울/.test(sky)) return '🌧️';
  if (/흐림/.test(sky)) return '☁️';
  if (/구름많음/.test(sky)) return '⛅';
  if (sky === '맑음') return '☀️';
  return '🌤️';
};

const formatDailyLabel = (item: DailyForecast) => {
  const offset = item.day_offset ?? 0;
  if (offset === 0) return '오늘';
  if (offset === 1) return '내일';
  if (offset === 2) return '모레';
  // 그 외는 요일 + 일자
  const md = new Date(item.date).toLocaleDateString('ko-KR', {
    timeZone: 'Asia/Seoul',
    month: 'short',
    day: 'numeric',
  });
  return `${item.weekday ?? ''} · ${md}`;
};

// 백엔드 farm_agent.weather_alerts.build_task_advisories 가 만드는 per-day 결정.
// status 의 의미:
//   blocked → critical advisory 또는 강수/적설 — 작업 자체 보류 권장
//   caution → warning level — 주의해서 진행
//   ok      → 임계 미달 — 정상 진행
interface TaskAdvisory {
  date: string;
  when: string;
  status: 'blocked' | 'caution' | 'ok';
  title: string;
  type: string;
  summary: string;
  actions: string[];
  crop_hint?: string | null;
}

export default function WeatherPage() {
  const [weather, setWeather] = useState<WeatherPayload | null>(null);
  const [taskAdvisories, setTaskAdvisories] = useState<TaskAdvisory[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState('');
  // 30초마다 갱신해 "N분 전" 라벨이 살아 움직이게 한다.
  const [nowTick, setNowTick] = useState(() => Date.now());
  const refreshButtonRef = useRef<HTMLButtonElement | null>(null);

  const loadWeather = useCallback(async (signal?: AbortSignal) => {
    setIsLoading(true);
    setError('');
    try {
      // 기상 페이로드와 agentic 작업 판단을 동시 호출 — 두 엔드포인트 모두
      // 같은 KMA 호출을 거치지만 각자 캐시되므로 직렬화할 이유 없음.
      const [weatherRes, advRes] = await Promise.all([
        fetch(`${FARMOS_API_BASE}/weather/current`, { credentials: 'include', signal }),
        fetch(`${FARMOS_API_BASE}/weather/task-advisories`, { credentials: 'include', signal }),
      ]);
      if (!weatherRes.ok) {
        throw new Error(`weather ${weatherRes.status}`);
      }
      setWeather(await weatherRes.json());
      // task-advisories 실패는 치명적이지 않다 — 페이지 나머지는 그대로 살아있게.
      if (advRes.ok) {
        const payload = (await advRes.json()) as { items?: TaskAdvisory[] };
        setTaskAdvisories(payload.items ?? []);
      } else {
        setTaskAdvisories([]);
      }
    } catch (err) {
      if ((err as Error).name !== 'AbortError') {
        setError('기상 데이터를 불러오지 못했습니다.');
      }
    } finally {
      if (!signal?.aborted) {
        setIsLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void loadWeather(controller.signal);
    return () => controller.abort();
  }, [loadWeather]);

  useEffect(() => {
    const id = window.setInterval(() => setNowTick(Date.now()), 30_000);
    return () => window.clearInterval(id);
  }, []);

  // `r` 단축키로 새로고침 — 입력 요소·IME 조합 중에는 무시. modifier 동반 시 브라우저 기본동작에 양보.
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (event.isComposing) return;
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      if (event.key !== 'r' && event.key !== 'R') return;
      const target = event.target as HTMLElement | null;
      const tag = target?.tagName;
      const isTyping =
        tag === 'INPUT' ||
        tag === 'TEXTAREA' ||
        tag === 'SELECT' ||
        target?.isContentEditable === true;
      if (isTyping) return;
      event.preventDefault();
      if (!isLoading) {
        void loadWeather();
        refreshButtonRef.current?.focus();
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [isLoading, loadWeather]);

  const forecasts = weather?.forecasts ?? [];
  const dailyForecasts = weather?.daily_forecasts ?? [];
  const current = weather?.current;

  const observedRel = formatRelativeTime(current?.observed_at ?? weather?.generated_at, nowTick);
  const generatedRel = formatRelativeTime(weather?.generated_at, nowTick);

  const alerts = useMemo(() => {
    const firstRain = forecasts.find((item) => (item.precipitation ?? 0) > 0);
    const firstWind = forecasts.find((item) => (item.wind_speed ?? 0) >= 7);
    return { firstRain, firstWind };
  }, [forecasts]);

  // 작업 판단은 백엔드 agentic policy (`build_task_advisories`) 가 결정한다.
  // 프런트엔드는 단순 렌더러 — 이 페이지의 if/else 가 farm-agent 의 판단과
  // 어긋날 가능성을 제거. status: blocked | caution | ok.
  const tasks = useMemo(
    () =>
      taskAdvisories.map((adv) => ({
        key: `${adv.date}-${adv.status}`,
        date: adv.date,
        when: adv.when,
        title: adv.title,
        description: adv.summary,
        type: adv.type,
        status: adv.status,
        blocked: adv.status === 'blocked',
        actions: adv.actions ?? [],
        cropHint: adv.crop_hint ?? null,
      })),
    [taskAdvisories],
  );

  // 첫 로딩(아직 페이로드 도착 전) 에서는 shape skeleton 으로 인지 지연을 줄인다.
  // 새로고침(이미 데이터 있음)은 헤더 spin 아이콘 + 제자리 업데이트로 처리하므로 skeleton 미사용.
  // 모든 hook 호출 후에 분기해 rules-of-hooks 준수.
  if (!weather && isLoading && !error) {
    return <WeatherSkeleton />;
  }

  return (
    <div className="space-y-5">
      <div className="card">
        <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
          <div>
            <div className="flex items-center gap-2 text-sm font-semibold text-[color:var(--color-info)]">
              <MdCloud className="text-lg" />
              <span>{weather?.source === 'kma' ? '기상청 초단기실황' : '모의 기상 데이터'}</span>
            </div>
            <h3 className="mt-2 text-2xl font-bold text-gray-950">
              {current?.temperature ?? '-'}°C
            </h3>
            <p className="mt-1 text-sm text-[color:var(--color-ink-mute)]">
              기준 {formatDateTime(current?.observed_at ?? weather?.generated_at)} · KST
              {observedRel && (
                <span className="ml-2 text-xs font-medium text-[color:var(--color-ink-faint)]" aria-live="polite">
                  ({observedRel})
                </span>
              )}
            </p>
          </div>
          <button
            ref={refreshButtonRef}
            type="button"
            className="btn-secondary inline-flex items-center gap-2 self-start"
            onClick={() => void loadWeather()}
            disabled={isLoading}
            title="새로고침 (R)"
            aria-keyshortcuts="R"
          >
            <MdRefresh className={isLoading ? 'animate-spin' : ''} />
            새로고침
            <kbd className="ml-1 hidden rounded border border-[color:var(--color-line)] bg-white px-1.5 py-0.5 text-[10px] font-semibold text-[color:var(--color-ink-mute)] sm:inline-block">
              R
            </kbd>
          </button>
        </div>

        {error ? (
          <div className="mt-4 rounded-lg border border-[color:var(--color-danger-light)] bg-[color:var(--color-danger-light)] px-4 py-3 text-sm text-[color:var(--color-danger)]">
            {error}
          </div>
        ) : (
          <div className="mt-5 grid grid-cols-2 gap-3 md:grid-cols-4">
            <div className="rounded-lg border border-[color:var(--color-line-soft)] bg-[color:var(--color-surface)] p-3">
              <p className="text-xs font-semibold text-[color:var(--color-ink-mute)]">습도</p>
              <p className="mt-1 text-lg font-bold text-gray-950">{current?.humidity ?? '-'}%</p>
            </div>
            <div className="rounded-lg border border-[color:var(--color-line-soft)] bg-[color:var(--color-surface)] p-3">
              <p className="text-xs font-semibold text-[color:var(--color-ink-mute)]">풍속</p>
              <p className="mt-1 text-lg font-bold text-gray-950">{current?.wind_speed ?? '-'}m/s</p>
            </div>
            <div className="rounded-lg border border-[color:var(--color-line-soft)] bg-[color:var(--color-surface)] p-3">
              <p className="text-xs font-semibold text-[color:var(--color-ink-mute)]">강수</p>
              <p className="mt-1 text-lg font-bold text-gray-950">{current?.precipitation ?? 0}mm</p>
            </div>
            <div className="rounded-lg border border-[color:var(--color-line-soft)] bg-[color:var(--color-surface)] p-3">
              <p className="text-xs font-semibold text-[color:var(--color-ink-mute)]">격자</p>
              <p className="mt-1 text-lg font-bold text-gray-950">
                {weather?.nx ?? '-'}, {weather?.ny ?? '-'}
              </p>
            </div>
          </div>
        )}
      </div>

      {(alerts.firstRain || alerts.firstWind) && (
        <div className="card border-amber-200 bg-[color:var(--tint-warning)]">
          <div className="flex items-start gap-3">
            <MdWarning className="mt-0.5 text-2xl text-[color:var(--color-accent-dark)]" />
            <div>
              <p className="font-bold text-amber-800">작업 전 기상 확인 필요</p>
              <p className="mt-1 text-sm text-amber-800">
                {alerts.firstRain
                  ? `${formatDateTime(alerts.firstRain.valid_at)} 강수 예보가 있습니다.`
                  : `${formatDateTime(alerts.firstWind?.valid_at)} 풍속이 높아질 수 있습니다.`}
              </p>
            </div>
          </div>
        </div>
      )}

      {/* 5일 일일 예보 (단기예보 0-2일 + 중기예보 3-4일) */}
      <div className="card !p-4 sm:!p-6">
        <div className="mb-4 flex items-center justify-between gap-3">
          <h3 className="section-title flex items-center gap-2">
            <MdCalendarMonth className="text-xl text-primary" />
            5일 예보
          </h3>
          <span className="text-xs font-semibold text-[color:var(--color-ink-mute)]">
            출처: 기상청 (단기 + 중기)
          </span>
        </div>
        <div className="overflow-x-auto -mx-1 px-1">
          <div className="grid grid-cols-5 gap-2 sm:gap-3" style={{ minWidth: '480px' }}>
            {(isLoading && dailyForecasts.length === 0
              ? Array.from({ length: 5 }, () => ({ date: '', day_offset: 0 } as DailyForecast))
              : dailyForecasts
            ).map((day, index) => {
              const isToday = day.day_offset === 0;
              return (
                <div
                  key={day.date || index}
                  className={`rounded-lg border p-3 text-center transition-all ${dailyTone(day)} ${
                    isToday ? 'ring-2 ring-primary/40' : ''
                  }`}
                >
                  <p className="text-xs font-bold text-[color:var(--color-ink-soft)]">{formatDailyLabel(day)}</p>
                  <p className="mt-2 text-2xl">{dailySkyEmoji(day)}</p>
                  <p className="mt-1 text-xs font-semibold text-[color:var(--color-ink-soft)]">{day.sky ?? '확인 어려움'}</p>
                  <p className="mt-2 text-sm font-bold text-gray-950">
                    {day.temp_min != null ? `${Math.round(day.temp_min)}°` : '-'}
                    <span className="mx-1 text-[color:var(--color-ink-disabled)]">/</span>
                    {day.temp_max != null ? (
                      <span className="text-[color:var(--color-danger)]">{Math.round(day.temp_max)}°</span>
                    ) : (
                      '-'
                    )}
                  </p>
                  <p className="mt-2 text-[11px] text-[color:var(--color-ink-mute)]">
                    강수 {day.precipitation_prob != null ? `${day.precipitation_prob}%` : '-'}
                  </p>
                  {day.wind_speed_max != null && (
                    <p className="text-[11px] text-[color:var(--color-ink-faint)]">바람 {day.wind_speed_max}m/s</p>
                  )}
                </div>
              );
            })}
          </div>
        </div>
        {dailyForecasts.length === 0 && !isLoading && (
          <p className="mt-3 text-xs text-[color:var(--color-ink-faint)]">
            중기예보 데이터가 아직 도착하지 않았습니다. 지역 코드(.env의 KMA_MID_LAND_REG_ID)를 확인해주세요.
          </p>
        )}
      </div>

      {/* 시간별 단기 예보 (오늘 ~6시간) */}
      <div className="card !p-4 sm:!p-6">
        <div className="mb-4 flex items-center justify-between gap-3">
          <h3 className="section-title">오늘 시간별 예보</h3>
          <span className="text-xs font-semibold text-[color:var(--color-ink-mute)]">
            갱신 {formatDateTime(weather?.generated_at)}
            {generatedRel && <span className="ml-1 text-[color:var(--color-ink-faint)]">({generatedRel})</span>}
          </span>
        </div>
        <div className="overflow-x-auto -mx-1 px-1">
          <div className="flex gap-2 sm:gap-3" style={{ minWidth: 'max-content' }}>
            {(isLoading && forecasts.length === 0 ? Array.from({ length: 4 }, () => ({})) : forecasts).map((item, index) => {
              const forecast = item as ForecastItem;
              return (
                <div
                  key={forecast.valid_at ?? index}
                  className={`w-[112px] rounded-lg border p-3 text-center transition-all sm:w-[136px] ${weatherTone(forecast)}`}
                >
                  <p className="text-xs font-semibold text-[color:var(--color-ink-mute)]">{formatDay(forecast)}</p>
                  <p className="mt-1 flex items-center justify-center gap-1 text-xs text-[color:var(--color-ink-mute)]">
                    <MdAccessTime />
                    {formatTime(forecast)}
                  </p>
                  <p className="mt-3 text-xl font-bold text-gray-950">
                    {forecast.temperature ?? '-'}°
                  </p>
                  <p className="mt-1 text-sm font-semibold text-[color:var(--color-ink-soft)]">{forecast.sky ?? '확인 중'}</p>
                  <p className="mt-2 text-xs text-[color:var(--color-ink-mute)]">
                    강수 {forecast.precipitation ?? 0}mm · 습도 {forecast.humidity ?? '-'}%
                  </p>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      <div className="card">
        <h3 className="section-title mb-1">작업 판단</h3>
        <p className="mb-4 text-xs text-[color:var(--color-ink-mute)]">
          5일 예보·작물 정보를 farm-agent 가 분석한 결정입니다.
        </p>
        <div className="space-y-3">
          {tasks.map((task) => {
            // status → tone tokens (blocked: red, caution: amber, ok: green).
            const tone =
              task.status === 'blocked'
                ? { card: 'border-[color:var(--color-danger-light)] bg-[color:var(--color-danger-light)]', icon: 'text-[color:var(--color-danger)]', badge: 'badge-danger' }
                : task.status === 'caution'
                  ? { card: 'border-amber-200 bg-[color:var(--tint-warning)]', icon: 'text-[color:var(--color-accent-dark)]', badge: 'badge-warning' }
                  : { card: 'border-[color:var(--color-primary-soft)] bg-[color:var(--color-primary-soft)]', icon: 'text-[color:var(--color-primary)]', badge: 'badge-success' };
            return (
            <div
              key={task.key}
              className={`rounded-lg border p-4 ${tone.card}`}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="flex min-w-0 items-start gap-3">
                  {task.blocked ? (
                    <MdBlock className={`mt-0.5 text-xl ${tone.icon}`} />
                  ) : (
                    <MdCheckCircle className={`mt-0.5 text-xl ${tone.icon}`} />
                  )}
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <h4 className="font-semibold text-gray-950">{task.title}</h4>
                      <span className={`badge text-xs ${tone.badge}`}>{task.type}</span>
                    </div>
                    <p className="mt-1 text-sm text-[color:var(--color-ink-soft)]">{task.description}</p>
                    {task.actions.length > 0 && (
                      <ul className="mt-2 space-y-1 text-xs text-[color:var(--color-ink-mute)]">
                        {task.actions.map((action, idx) => (
                          <li key={idx} className="flex gap-1.5">
                            <span aria-hidden>•</span>
                            <span>{action}</span>
                          </li>
                        ))}
                      </ul>
                    )}
                    {task.cropHint && (
                      <p className="mt-2 rounded-md bg-white/60 px-2 py-1 text-xs text-[color:var(--color-ink-soft)]">
                        🌱 {task.cropHint}
                      </p>
                    )}
                  </div>
                </div>
                <span className="whitespace-nowrap text-sm font-medium text-[color:var(--color-ink-mute)]">
                  {task.date
                    ? new Date(task.date).toLocaleDateString('ko-KR', {
                        timeZone: 'Asia/Seoul',
                        month: 'short',
                        day: 'numeric',
                        weekday: 'short',
                      })
                    : '확인 중'}
                </span>
              </div>
            </div>
            );
          })}
          {!isLoading && tasks.length === 0 && (
            <p className="rounded-lg border border-[color:var(--color-line-soft)] bg-[color:var(--color-surface)] px-4 py-3 text-sm text-[color:var(--color-ink-mute)]">
              예보 데이터가 아직 없습니다.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
