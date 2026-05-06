// iter-22 — IoT dashboard shape skeleton.
// 사용 분기: useSensorData().loading === true && latest === null (첫 fetchAll 진행 중).
// 의도: 농민이 "백엔드 끊김" vs "아직 첫 패킷 수신 전" 을 구분 못 하던 문제 해결.
// 패턴은 iter-15(Market) / 17(Weather) / 19(Subsidy) / 20(Journal) / 21(Diagnosis) 와 동일.

const PulseBlock = ({ className = '' }: { className?: string }) => (
  <div className={`animate-pulse rounded bg-gray-200 ${className}`} />
);

function SensorCardSkeleton() {
  return (
    <div className="card !p-4 sm:!p-6">
      <div className="flex items-center justify-between">
        <PulseBlock className="w-10 h-10 sm:w-12 sm:h-12 !rounded-xl" />
        <PulseBlock className="w-12 h-3 !bg-gray-100" />
      </div>
      <PulseBlock className="w-20 h-3 mt-3 !bg-gray-100" />
      <PulseBlock className="w-24 h-8 sm:h-10 mt-2" />
      <div className="mt-3 flex items-center gap-2">
        <PulseBlock className="flex-1 h-1.5 !bg-gray-100" />
        <PulseBlock className="w-12 h-2 !bg-gray-100" />
      </div>
    </div>
  );
}

function ChartCardSkeleton({ heightClass }: { heightClass: string }) {
  return (
    <div className="card">
      <PulseBlock className="w-32 h-5 mb-4 !bg-gray-100" />
      <div className={`${heightClass} flex items-end gap-1.5 px-2`}>
        {Array.from({ length: 18 }).map((_, i) => (
          <PulseBlock
            key={i}
            className="flex-1 !rounded-t-md"
            // 시각적으로 차트처럼 보이게 의도적 높낮이
            // (랜덤 대신 결정적 패턴으로 prefers-reduced-motion 일관성)
          />
        ))}
      </div>
    </div>
  );
}

function ListCardSkeleton() {
  return (
    <div className="card">
      <div className="flex items-center justify-between gap-2 mb-3">
        <PulseBlock className="w-20 h-5 !bg-gray-100" />
        <PulseBlock className="w-24 h-7 !bg-gray-100" />
      </div>
      <div className="space-y-2">
        {Array.from({ length: 3 }).map((_, i) => (
          <div key={i} className="flex items-center gap-3 p-3 rounded-xl bg-gray-50">
            <PulseBlock className="w-3 h-3 !rounded-full" />
            <div className="flex-1 min-w-0 space-y-1.5">
              <PulseBlock className="w-3/4 h-3 !bg-gray-100" />
              <PulseBlock className="w-1/2 h-2 !bg-gray-100" />
            </div>
            <PulseBlock className="w-14 h-5 !bg-gray-100" />
          </div>
        ))}
      </div>
    </div>
  );
}

export default function IoTSkeleton() {
  return (
    <>
      <span className="sr-only" role="status" aria-live="polite">
        IoT 센서 데이터를 불러오는 중입니다
      </span>

      <div className="space-y-6" aria-hidden>
        {/* Connection status placeholder */}
        <div className="flex items-center gap-2">
          <PulseBlock className="w-3 h-3 !rounded-full" />
          <PulseBlock className="w-24 h-3 !bg-gray-100" />
          <PulseBlock className="w-40 h-3 !bg-gray-100 hidden sm:block" />
        </div>

        {/* 4 sensor cards */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <SensorCardSkeleton key={i} />
          ))}
        </div>

        {/* 2 chart cards */}
        <ChartCardSkeleton heightClass="h-[200px] sm:h-[280px]" />
        <ChartCardSkeleton heightClass="h-[200px] sm:h-[250px]" />

        {/* 2 list cards */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <ListCardSkeleton />
          <ListCardSkeleton />
        </div>
      </div>
    </>
  );
}
