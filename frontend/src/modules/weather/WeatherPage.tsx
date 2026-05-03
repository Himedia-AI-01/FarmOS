import { useCallback, useEffect, useMemo, useState } from 'react';
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
  if (isRainy(item)) return 'border-blue-200 bg-blue-50';
  if ((item.wind_speed ?? 0) >= 7) return 'border-amber-200 bg-amber-50';
  if (item.sky === '맑음') return 'border-green-200 bg-green-50';
  return 'border-gray-100 bg-gray-50';
};

// 일일 예보 카드 톤 — sky 텍스트 또는 부분 매칭(예: "흐리고 비")으로 판정
const dailyTone = (item: DailyForecast) => {
  const sky = item.sky ?? '';
  if (
    (item.precipitation ?? 0) > 0 ||
    /비|눈|빗방울|진눈깨비|소나기/.test(sky)
  ) {
    return 'border-blue-200 bg-blue-50';
  }
  if ((item.precipitation_prob ?? 0) >= 60) return 'border-blue-100 bg-blue-50/60';
  if ((item.wind_speed_max ?? 0) >= 7) return 'border-amber-200 bg-amber-50';
  if (sky === '맑음') return 'border-green-200 bg-green-50';
  if (/흐림/.test(sky)) return 'border-gray-200 bg-gray-100';
  return 'border-gray-100 bg-gray-50';
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

export default function WeatherPage() {
  const [weather, setWeather] = useState<WeatherPayload | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState('');

  const loadWeather = useCallback(async (signal?: AbortSignal) => {
    setIsLoading(true);
    setError('');
    try {
      const response = await fetch(`${FARMOS_API_BASE}/weather/current`, {
        credentials: 'include',
        signal,
      });
      if (!response.ok) {
        throw new Error(`weather ${response.status}`);
      }
      setWeather(await response.json());
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

  const forecasts = weather?.forecasts ?? [];
  const dailyForecasts = weather?.daily_forecasts ?? [];
  const current = weather?.current;

  const alerts = useMemo(() => {
    const firstRain = forecasts.find((item) => (item.precipitation ?? 0) > 0);
    const firstWind = forecasts.find((item) => (item.wind_speed ?? 0) >= 7);
    return { firstRain, firstWind };
  }, [forecasts]);

  const tasks = useMemo(() => {
    const next = forecasts.slice(0, 4);
    return next.map((item) => {
      if ((item.precipitation ?? 0) > 0) {
        return {
          key: `${item.valid_at}-rain`,
          date: item.valid_at,
          title: '실외 방제 보류',
          description: '강수 예보가 있어 약제 살포와 노지 작업은 피하는 편이 좋습니다.',
          type: '방제',
          blocked: true,
        };
      }
      if ((item.wind_speed ?? 0) >= 7) {
        return {
          key: `${item.valid_at}-wind`,
          date: item.valid_at,
          title: '살포 전 풍속 확인',
          description: '바람이 강해질 수 있어 분무 작업 전 현장 풍속을 다시 확인하세요.',
          type: '주의',
          blocked: false,
        };
      }
      return {
        key: `${item.valid_at}-ok`,
        date: item.valid_at,
        title: '예정 작업 진행 가능',
        description: '초단기 예보 기준으로 큰 기상 제한은 보이지 않습니다.',
        type: '작업',
        blocked: false,
      };
    });
  }, [forecasts]);

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
            <div className="flex items-center gap-2 text-sm font-semibold text-blue-700">
              <MdCloud className="text-lg" />
              <span>{weather?.source === 'kma' ? '기상청 초단기실황' : '모의 기상 데이터'}</span>
            </div>
            <h3 className="mt-2 text-2xl font-bold text-gray-950">
              {current?.temperature ?? '-'}°C
            </h3>
            <p className="mt-1 text-sm text-gray-500">
              기준 {formatDateTime(current?.observed_at ?? weather?.generated_at)} · KST
            </p>
          </div>
          <button
            type="button"
            className="btn-secondary inline-flex items-center gap-2 self-start"
            onClick={() => void loadWeather()}
            disabled={isLoading}
          >
            <MdRefresh className={isLoading ? 'animate-spin' : ''} />
            새로고침
          </button>
        </div>

        {error ? (
          <div className="mt-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {error}
          </div>
        ) : (
          <div className="mt-5 grid grid-cols-2 gap-3 md:grid-cols-4">
            <div className="rounded-lg border border-gray-100 bg-gray-50 p-3">
              <p className="text-xs font-semibold text-gray-500">습도</p>
              <p className="mt-1 text-lg font-bold text-gray-950">{current?.humidity ?? '-'}%</p>
            </div>
            <div className="rounded-lg border border-gray-100 bg-gray-50 p-3">
              <p className="text-xs font-semibold text-gray-500">풍속</p>
              <p className="mt-1 text-lg font-bold text-gray-950">{current?.wind_speed ?? '-'}m/s</p>
            </div>
            <div className="rounded-lg border border-gray-100 bg-gray-50 p-3">
              <p className="text-xs font-semibold text-gray-500">강수</p>
              <p className="mt-1 text-lg font-bold text-gray-950">{current?.precipitation ?? 0}mm</p>
            </div>
            <div className="rounded-lg border border-gray-100 bg-gray-50 p-3">
              <p className="text-xs font-semibold text-gray-500">격자</p>
              <p className="mt-1 text-lg font-bold text-gray-950">
                {weather?.nx ?? '-'}, {weather?.ny ?? '-'}
              </p>
            </div>
          </div>
        )}
      </div>

      {(alerts.firstRain || alerts.firstWind) && (
        <div className="card border-amber-200 bg-amber-50">
          <div className="flex items-start gap-3">
            <MdWarning className="mt-0.5 text-2xl text-amber-600" />
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
          <span className="text-xs font-semibold text-gray-500">
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
                  <p className="text-xs font-bold text-gray-700">{formatDailyLabel(day)}</p>
                  <p className="mt-2 text-2xl">{dailySkyEmoji(day)}</p>
                  <p className="mt-1 text-xs font-semibold text-gray-700">{day.sky ?? '확인 어려움'}</p>
                  <p className="mt-2 text-sm font-bold text-gray-950">
                    {day.temp_min != null ? `${Math.round(day.temp_min)}°` : '-'}
                    <span className="mx-1 text-gray-300">/</span>
                    {day.temp_max != null ? (
                      <span className="text-red-600">{Math.round(day.temp_max)}°</span>
                    ) : (
                      '-'
                    )}
                  </p>
                  <p className="mt-2 text-[11px] text-gray-500">
                    강수 {day.precipitation_prob != null ? `${day.precipitation_prob}%` : '-'}
                  </p>
                  {day.wind_speed_max != null && (
                    <p className="text-[11px] text-gray-400">바람 {day.wind_speed_max}m/s</p>
                  )}
                </div>
              );
            })}
          </div>
        </div>
        {dailyForecasts.length === 0 && !isLoading && (
          <p className="mt-3 text-xs text-gray-400">
            중기예보 데이터가 아직 도착하지 않았습니다. 지역 코드(.env의 KMA_MID_LAND_REG_ID)를 확인해주세요.
          </p>
        )}
      </div>

      {/* 시간별 단기 예보 (오늘 ~6시간) */}
      <div className="card !p-4 sm:!p-6">
        <div className="mb-4 flex items-center justify-between gap-3">
          <h3 className="section-title">오늘 시간별 예보</h3>
          <span className="text-xs font-semibold text-gray-500">
            갱신 {formatDateTime(weather?.generated_at)}
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
                  <p className="text-xs font-semibold text-gray-600">{formatDay(forecast)}</p>
                  <p className="mt-1 flex items-center justify-center gap-1 text-xs text-gray-500">
                    <MdAccessTime />
                    {formatTime(forecast)}
                  </p>
                  <p className="mt-3 text-xl font-bold text-gray-950">
                    {forecast.temperature ?? '-'}°
                  </p>
                  <p className="mt-1 text-sm font-semibold text-gray-700">{forecast.sky ?? '확인 중'}</p>
                  <p className="mt-2 text-xs text-gray-500">
                    강수 {forecast.precipitation ?? 0}mm · 습도 {forecast.humidity ?? '-'}%
                  </p>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      <div className="card">
        <h3 className="section-title mb-4">작업 판단</h3>
        <div className="space-y-3">
          {tasks.map((task) => (
            <div
              key={task.key}
              className={`rounded-lg border p-4 ${
                task.blocked ? 'border-red-200 bg-red-50' : 'border-green-200 bg-green-50'
              }`}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="flex min-w-0 items-start gap-3">
                  {task.blocked ? (
                    <MdBlock className="mt-0.5 text-xl text-red-600" />
                  ) : (
                    <MdCheckCircle className="mt-0.5 text-xl text-green-600" />
                  )}
                  <div>
                    <div className="flex flex-wrap items-center gap-2">
                      <h4 className="font-semibold text-gray-950">{task.title}</h4>
                      <span className={`badge text-xs ${task.blocked ? 'badge-danger' : 'badge-success'}`}>
                        {task.type}
                      </span>
                    </div>
                    <p className="mt-1 text-sm text-gray-700">{task.description}</p>
                  </div>
                </div>
                <span className="whitespace-nowrap text-sm font-medium text-gray-500">
                  {formatDateTime(task.date)}
                </span>
              </div>
            </div>
          ))}
          {!isLoading && tasks.length === 0 && (
            <p className="rounded-lg border border-gray-100 bg-gray-50 px-4 py-3 text-sm text-gray-500">
              예보 데이터가 아직 없습니다.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
