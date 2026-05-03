// 공익직불사업 페이지 초기 로딩 스켈레톤 — SubsidyPage 의 카드 레이아웃(안내 헤더 / 3 요약 타일 /
// 결과 카드 리스트) shape 만 animate-pulse 회색 블록으로 모사한다. 농민이 자격 확인을 시작하는
// 첫 화면이라 "공익직불사업 자격을 확인하는 중..." 텍스트 한 줄보다 형태감 있는 골격이
// 인지 지연을 줄인다. SR 사용자에겐 sr-only role="status" 로 안내, 시각 데코는 aria-hidden.

const RESULT_CARD_COUNT = 4;

export default function SubsidySkeleton() {
  return (
    <>
      <span className="sr-only" role="status" aria-live="polite">
        공익직불사업 자격을 확인하는 중입니다.
      </span>
      <div className="space-y-6 animate-pulse" aria-hidden>
        {/* 안내 헤더 카드 */}
        <div className="card bg-gradient-to-r from-gray-50 to-gray-100/60 border-gray-200">
          <div className="flex items-start gap-3">
            <div className="h-10 w-10 flex-shrink-0 rounded-lg bg-gray-200" />
            <div className="flex-1 space-y-2">
              <div className="h-5 w-64 max-w-full rounded bg-gray-200" />
              <div className="h-3 w-full rounded bg-gray-100" />
              <div className="h-3 w-5/6 rounded bg-gray-100" />
            </div>
          </div>
        </div>

        {/* 결과 요약 3 타일 */}
        <div className="grid grid-cols-3 gap-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <div
              key={i}
              className="rounded-2xl border-2 border-gray-200 bg-gray-50 p-4 text-center"
            >
              <div className="mx-auto h-8 w-10 rounded bg-gray-200" />
              <div className="mx-auto mt-2 h-3 w-16 rounded bg-gray-100" />
            </div>
          ))}
        </div>

        {/* 지원금 카드 목록 */}
        <div className="space-y-3">
          {Array.from({ length: RESULT_CARD_COUNT }).map((_, i) => (
            <div
              key={i}
              className="card border-2 border-gray-200 bg-gray-50/50"
            >
              <div className="flex items-start gap-3">
                <div className="h-8 w-8 flex-shrink-0 rounded-full bg-gray-200" />
                <div className="flex-1 space-y-2">
                  <div className="flex items-center gap-2">
                    <div className="h-5 w-40 rounded bg-gray-200" />
                    <div className="h-5 w-16 rounded-full bg-gray-200" />
                  </div>
                  <div className="h-4 w-32 rounded bg-gray-100" />
                  <div className="h-3 w-full rounded bg-gray-100" />
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </>
  );
}
