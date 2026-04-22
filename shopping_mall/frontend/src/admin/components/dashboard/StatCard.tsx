interface StatCardProps {
  title: string;
  value: string;
  /** Percentage change vs previous period (positive = up, negative = down). null = no comparison available. */
  change?: number | null;
  /** Material Symbols Outlined icon name */
  icon: string;
  iconBgClass?: string;
  iconColorClass?: string;
  /** Optional badge label override when change isn't a % (e.g. "+5건") */
  changeSuffix?: string;
  /** If true, a positive change is shown in red (for metrics where more = worse, e.g. pending tickets) */
  invertTrend?: boolean;
}

export default function StatCard({
  title,
  value,
  change,
  icon,
  iconBgClass = 'bg-emerald-50',
  iconColorClass = 'text-emerald-600',
  changeSuffix = '%',
  invertTrend = false,
}: StatCardProps) {
  const hasChange = change != null && !isNaN(change);
  const isPositive = hasChange && change! >= 0;
  // If invertTrend, positive change is bad (red), negative is good (green)
  const isGood = invertTrend ? !isPositive : isPositive;

  return (
    <div className="bg-white p-6 rounded-3xl shadow-sm flex flex-col gap-4 border border-stone-100 hover:border-emerald-100 transition-all">
      {/* Header row */}
      <div className="flex items-center justify-between">
        <span className="text-[10px] uppercase tracking-widest font-bold text-stone-400">
          {title}
        </span>
        <div className={`p-2 ${iconBgClass} ${iconColorClass} rounded-xl`}>
          <span className="material-symbols-outlined text-[20px]" aria-hidden="true">
            {icon}
          </span>
        </div>
      </div>

      {/* Value + trend badge */}
      <div className="flex items-end gap-2">
        <h2 className="text-3xl font-bold tracking-tight text-stone-900">{value}</h2>
        {hasChange ? (
          <span
            className={`text-xs font-bold mb-1.5 flex items-center gap-0.5 px-1.5 py-0.5 rounded-md ${
              isGood
                ? 'bg-emerald-50 text-emerald-600'
                : 'bg-red-50 text-red-500'
            }`}
          >
            <span className="material-symbols-outlined text-xs" aria-hidden="true">
              {isPositive ? 'trending_up' : 'trending_down'}
            </span>
            {isPositive ? '+' : ''}
            {typeof change === 'number' ? change.toFixed(changeSuffix === '%' ? 1 : 0) : change}
            {changeSuffix}
          </span>
        ) : (
          <span className="text-stone-400 text-xs font-bold mb-1.5 px-1.5 py-0.5">유지</span>
        )}
      </div>
    </div>
  );
}
