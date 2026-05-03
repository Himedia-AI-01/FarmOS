// 영농일지 타임라인 초기 로딩 스켈레톤 — JournalPage 의 날짜 그룹 + 카드 shape 만
// animate-pulse 회색 블록으로 모사한다. 농민이 매일 가장 자주 보는 surface 라
// "불러오는 중..." 텍스트 한 줄보다 형태감 있는 골격이 인지 지연을 줄인다.
// 액션 바·DailyJournalPanel·MissingFieldsAlert·필터 pill 은 이미 페이지에서
// 자체 렌더되므로 본 컴포넌트는 타임라인 영역만 교체한다.
// SR 사용자에겐 sr-only role="status" 로 안내, 시각 데코는 aria-hidden.

const DATE_GROUPS: ReadonlyArray<{ cardCount: number }> = [
  { cardCount: 3 },
  { cardCount: 2 },
];

export default function JournalSkeleton() {
  return (
    <>
      <span className="sr-only" role="status" aria-live="polite">
        영농일지를 불러오는 중입니다.
      </span>
      <div className="space-y-0 animate-pulse" aria-hidden>
        {DATE_GROUPS.map((group, gi) => (
          <div key={gi}>
            {/* 날짜 헤더 */}
            <div className="flex items-center gap-3 py-2">
              <div className="h-4 w-20 rounded bg-gray-200" />
              <div className="flex-1 h-px bg-gray-200" />
            </div>

            {Array.from({ length: group.cardCount }).map((_, ci) => (
              <div key={ci} className="flex gap-4 relative">
                {/* 타임라인 도트 + 라인 */}
                <div className="flex flex-col items-center">
                  <div className="w-3 h-3 rounded-full bg-gray-200 z-10" />
                  {ci < group.cardCount - 1 && (
                    <div className="w-0.5 flex-1 bg-gray-100" />
                  )}
                </div>

                <div className="flex-1 pb-4">
                  <div className="p-4 rounded-xl bg-white border border-gray-100">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2 flex-wrap">
                        {/* 작업단계 badge */}
                        <div className="h-5 w-16 rounded-full bg-gray-200" />
                        {/* 작목 */}
                        <div className="h-3 w-12 rounded bg-gray-100" />
                        {/* 필지 */}
                        <div className="h-3 w-16 rounded bg-gray-100" />
                        {/* 날씨 */}
                        <div className="h-3 w-10 rounded bg-gray-100" />
                      </div>
                      <div className="flex gap-1">
                        <div className="h-5 w-5 rounded bg-gray-100" />
                        <div className="h-5 w-5 rounded bg-gray-100" />
                      </div>
                    </div>

                    {/* detail 한 줄 */}
                    <div className="mt-3 h-3 w-4/5 rounded bg-gray-100" />
                  </div>
                </div>
              </div>
            ))}
          </div>
        ))}
      </div>
    </>
  );
}
