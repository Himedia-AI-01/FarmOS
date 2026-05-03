// 기상 페이지 초기 로딩 스켈레톤 — WeatherPage 의 카드 5개(헤더 / 5일 예보 / 시간별 / 작업 판단)
// shape 만 animate-pulse 회색 블록으로 모사한다. 농민이 매일 가장 자주 보는 페이지라
// "마이너스 자리 표시(-°C, -%)" 보다 형태감 있는 골격이 인지 지연을 더 줄인다.
// SR 사용자에겐 sr-only role="status" 로 안내, 시각 데코는 aria-hidden 으로 노이즈 차단.

export default function WeatherSkeleton() {
  return (
    <>
      <span className="sr-only" role="status" aria-live="polite">
        기상 정보를 불러오는 중입니다.
      </span>
      <div className="space-y-5 animate-pulse" aria-hidden>
        {/* 헤더 카드 — 현재 기온 + 4 메트릭 grid */}
        <div className="card">
          <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
            <div className="space-y-2">
              <div className="flex items-center gap-2">
                <div className="h-5 w-5 rounded bg-gray-200" />
                <div className="h-4 w-32 rounded bg-gray-200" />
              </div>
              <div className="h-8 w-24 rounded bg-gray-200" />
              <div className="h-3 w-48 rounded bg-gray-100" />
            </div>
            <div className="h-10 w-28 self-start rounded-lg bg-gray-100" />
          </div>
          <div className="mt-5 grid grid-cols-2 gap-3 md:grid-cols-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <div
                key={i}
                className="rounded-lg border border-gray-100 bg-gray-50 p-3"
              >
                <div className="h-3 w-12 rounded bg-gray-200" />
                <div className="mt-2 h-5 w-16 rounded bg-gray-200" />
              </div>
            ))}
          </div>
        </div>

        {/* 5일 예보 카드 */}
        <div className="card !p-4 sm:!p-6">
          <div className="mb-4 flex items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <div className="h-5 w-5 rounded bg-gray-200" />
              <div className="h-4 w-20 rounded bg-gray-200" />
            </div>
            <div className="h-3 w-32 rounded bg-gray-100" />
          </div>
          <div className="overflow-x-auto -mx-1 px-1">
            <div className="grid grid-cols-5 gap-2 sm:gap-3" style={{ minWidth: '480px' }}>
              {Array.from({ length: 5 }).map((_, i) => (
                <div
                  key={i}
                  className="rounded-lg border border-gray-100 bg-gray-50 p-3 text-center"
                >
                  <div className="mx-auto h-3 w-10 rounded bg-gray-200" />
                  <div className="mx-auto mt-2 h-7 w-7 rounded-full bg-gray-200" />
                  <div className="mx-auto mt-2 h-3 w-12 rounded bg-gray-100" />
                  <div className="mx-auto mt-2 h-4 w-16 rounded bg-gray-200" />
                  <div className="mx-auto mt-2 h-3 w-14 rounded bg-gray-100" />
                  <div className="mx-auto mt-1 h-3 w-12 rounded bg-gray-100" />
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* 시간별 단기 예보 */}
        <div className="card !p-4 sm:!p-6">
          <div className="mb-4 flex items-center justify-between gap-3">
            <div className="h-4 w-32 rounded bg-gray-200" />
            <div className="h-3 w-28 rounded bg-gray-100" />
          </div>
          <div className="overflow-x-auto -mx-1 px-1">
            <div className="flex gap-2 sm:gap-3" style={{ minWidth: 'max-content' }}>
              {Array.from({ length: 4 }).map((_, i) => (
                <div
                  key={i}
                  className="w-[112px] rounded-lg border border-gray-100 bg-gray-50 p-3 text-center sm:w-[136px]"
                >
                  <div className="mx-auto h-3 w-12 rounded bg-gray-200" />
                  <div className="mx-auto mt-1 h-3 w-16 rounded bg-gray-100" />
                  <div className="mx-auto mt-3 h-5 w-12 rounded bg-gray-200" />
                  <div className="mx-auto mt-1 h-3 w-12 rounded bg-gray-100" />
                  <div className="mx-auto mt-2 h-3 w-20 rounded bg-gray-100" />
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* 작업 판단 카드 */}
        <div className="card">
          <div className="mb-4 h-5 w-20 rounded bg-gray-200" />
          <div className="space-y-3">
            {Array.from({ length: 3 }).map((_, i) => (
              <div
                key={i}
                className="rounded-lg border border-gray-100 bg-gray-50 p-4"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="flex min-w-0 items-start gap-3">
                    <div className="mt-0.5 h-5 w-5 rounded bg-gray-200" />
                    <div className="space-y-2">
                      <div className="flex items-center gap-2">
                        <div className="h-4 w-32 rounded bg-gray-200" />
                        <div className="h-4 w-12 rounded-full bg-gray-200" />
                      </div>
                      <div className="h-3 w-56 rounded bg-gray-100" />
                    </div>
                  </div>
                  <div className="h-3 w-24 rounded bg-gray-100" />
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </>
  );
}
