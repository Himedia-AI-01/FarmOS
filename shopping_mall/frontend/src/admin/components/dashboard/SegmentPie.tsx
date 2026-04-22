import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer } from 'recharts';

// Brand-consistent emerald/stone palette
const SEGMENT_COLORS = [
  '#006933', // primary
  '#406748', // secondary
  '#bdcabc', // outline-variant
  '#71dc8e', // primary-fixed-dim
  '#a6d1ac', // secondary-fixed-dim
  '#9d364a', // tertiary
];

const SEGMENT_LABELS: Record<string, string> = {
  vip: 'VIP',
  loyal: '충성 고객',
  repeat: '재구매',
  new: '신규',
  at_risk: '이탈 위험',
  dormant: '휴면',
};

interface SegmentPieProps {
  data: { name: string; count: number }[];
}

export default function SegmentPie({ data }: SegmentPieProps) {
  const chartData = data.map((d) => ({
    name: SEGMENT_LABELS[d.name] ?? d.name,
    value: d.count,
    rawName: d.name,
  }));

  const total = chartData.reduce((sum, d) => sum + d.value, 0);

  return (
    <div className="bg-white p-8 rounded-3xl shadow-sm flex flex-col border border-stone-100 space-y-6">
      {/* Header */}
      <div>
        <h3 className="text-lg font-bold text-stone-900">주문 세그먼트</h3>
        <p className="text-xs text-stone-400">고객 유형별 판매 비중</p>
      </div>

      {/* Donut chart */}
      <div className="relative flex items-center justify-center">
        <ResponsiveContainer width="100%" height={180}>
          <PieChart>
            <Pie
              data={chartData}
              cx="50%"
              cy="50%"
              innerRadius={55}
              outerRadius={80}
              dataKey="value"
              startAngle={90}
              endAngle={-270}
              strokeWidth={2}
              stroke="#ffffff"
            >
              {chartData.map((_, index) => (
                <Cell
                  key={index}
                  fill={SEGMENT_COLORS[index % SEGMENT_COLORS.length]}
                />
              ))}
            </Pie>
            <Tooltip
              contentStyle={{
                borderRadius: '12px',
                border: '1px solid #e7e5e4',
                boxShadow: '0 4px 16px -4px rgba(0,0,0,0.08)',
                fontSize: 12,
              }}
              formatter={(value) => [`${value}명`, '고객 수']}
            />
          </PieChart>
        </ResponsiveContainer>
        {/* Center label */}
        <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
          <span className="text-2xl font-bold text-stone-900">
            {total >= 1000 ? `${(total / 1000).toFixed(1)}k` : String(total)}
          </span>
          <span className="text-[10px] text-stone-400 uppercase tracking-wider font-bold">
            Total
          </span>
        </div>
      </div>

      {/* Legend */}
      <div className="space-y-2.5">
        {chartData.map((item, index) => {
          const pct = total > 0 ? ((item.value / total) * 100).toFixed(0) : '0';
          return (
            <div key={item.rawName} className="flex items-center justify-between text-sm">
              <div className="flex items-center gap-2">
                <div
                  className="w-2 h-2 rounded-full shrink-0"
                  style={{ backgroundColor: SEGMENT_COLORS[index % SEGMENT_COLORS.length] }}
                  aria-hidden="true"
                />
                <span className="text-stone-600">{item.name}</span>
              </div>
              <span className="font-bold text-stone-900">{pct}%</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
