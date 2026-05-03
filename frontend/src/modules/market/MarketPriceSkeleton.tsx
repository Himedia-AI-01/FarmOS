// 시세 페이지 초기 로딩 스켈레톤 — 실제 페이지 레이아웃을 그대로 모사해 인지 지연을 줄인다.
// 토글, 주요 변동 카드, 검색 바, 부류 필터, 가격 테이블의 shape 만 animate-pulse 회색 블록으로 표시.
// SR 사용자에겐 sr-only role="status" 로 "불러오는 중" 안내, 시각 사용자에겐 aria-hidden 으로 노이즈 차단.

const CATEGORY_PILL_WIDTHS = [56, 72, 88, 64, 80, 72] as const;

export default function MarketPriceSkeleton() {
  return (
    <>
      <span className="sr-only" role="status" aria-live="polite">
        시세 정보를 불러오는 중입니다.
      </span>
      <div className="space-y-6 animate-pulse" aria-hidden>
        {/* 소매/도매 토글 */}
        <div className="flex items-center gap-2">
          <div className="h-9 w-24 rounded-xl bg-gray-200" />
          <div className="h-9 w-24 rounded-xl bg-gray-100" />
        </div>

        {/* 주요 가격 변동 카드 */}
        <div className="card bg-gradient-to-r from-gray-50 to-gray-100/60 border-gray-200">
          <div className="mb-3 flex items-center gap-2">
            <div className="h-5 w-5 rounded bg-gray-200" />
            <div className="h-4 w-28 rounded bg-gray-200" />
            <div className="ml-auto h-3 w-40 rounded bg-gray-100" />
          </div>
          <div className="space-y-2">
            {[0, 1, 2].map((i) => (
              <div
                key={i}
                className="flex items-center justify-between rounded-xl bg-white/70 p-3"
              >
                <div className="flex items-center gap-2">
                  <div className="h-4 w-4 rounded bg-gray-200" />
                  <div className="h-4 w-24 rounded bg-gray-200" />
                  <div className="h-3 w-12 rounded bg-gray-100" />
                </div>
                <div className="flex items-center gap-3">
                  <div className="h-3 w-32 rounded bg-gray-100" />
                  <div className="h-5 w-14 rounded-full bg-gray-200" />
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* 검색 바 */}
        <div className="h-10 w-full rounded-xl bg-gray-100" />

        {/* 부류 필터 pill */}
        <div className="flex flex-wrap gap-2">
          {CATEGORY_PILL_WIDTHS.map((w, i) => (
            <div
              key={i}
              className="h-8 rounded-lg bg-gray-100"
              style={{ width: `${w}px` }}
            />
          ))}
        </div>

        {/* 가격 목록 테이블 */}
        <div className="card">
          <div className="mb-4 flex items-center justify-between">
            <div className="h-5 w-32 rounded bg-gray-200" />
            <div className="h-3 w-16 rounded bg-gray-100" />
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-200">
                  {Array.from({ length: 7 }).map((_, i) => (
                    <th key={i} className="px-4 py-3">
                      <div className="mx-auto h-3 w-12 rounded bg-gray-200" />
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {Array.from({ length: 8 }).map((_, r) => (
                  <tr key={r} className="border-b border-gray-50">
                    {Array.from({ length: 7 }).map((__, c) => (
                      <td key={c} className="px-4 py-3">
                        <div
                          className="h-3 rounded bg-gray-100"
                          style={{ width: c === 0 ? '64%' : '40%' }}
                        />
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </>
  );
}
