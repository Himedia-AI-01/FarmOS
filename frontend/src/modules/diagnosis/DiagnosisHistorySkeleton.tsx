// 진단 기록(history) 초기 fetch 동안 표시되는 shape skeleton.
// 실제 카드 레이아웃(체크박스 + badge/title + meta + 삭제/chat 버튼) 을 그대로 모방해
// "로딩 중" 인지 "빈 기록" 인지 농민이 구분 가능하게 한다.
// iter-15(Market) / 17(Weather) / 19(Subsidy) / 20(Journal) 의 검증된 패턴을 동일 적용.

interface Props {
  count?: number;
}

export default function DiagnosisHistorySkeleton({ count = 4 }: Props) {
  return (
    <>
      <span className="sr-only" role="status" aria-live="polite">
        진단 기록을 불러오는 중입니다.
      </span>
      <div className="grid grid-cols-1 gap-3" aria-hidden="true">
        {Array.from({ length: count }).map((_, i) => (
          <div
            key={i}
            className="w-full p-4 rounded-2xl border border-primary/10 bg-white flex items-center justify-between shadow-sm animate-pulse"
          >
            <div className="flex items-center gap-4 flex-1 min-w-0">
              {/* 체크박스 자리 */}
              <div className="w-6 h-6 rounded-lg bg-gray-100 flex-shrink-0" />

              <div className="flex-1 space-y-2 min-w-0">
                {/* 작목 badge + 해충명 */}
                <div className="flex items-center gap-2">
                  <div className="h-4 w-12 rounded bg-gray-200" />
                  <div className="h-4 w-28 rounded bg-gray-200" />
                </div>
                {/* 지역 · 날짜 */}
                <div className="h-3 w-44 rounded bg-gray-100" />
              </div>
            </div>

            <div className="flex items-center gap-3 flex-shrink-0">
              {/* 삭제 버튼 자리 */}
              <div className="w-9 h-9 rounded-xl bg-gray-100" />
              {/* chat 진입 자리 */}
              <div className="w-10 h-10 rounded-full bg-gray-100" />
            </div>
          </div>
        ))}
      </div>
    </>
  );
}
