import { useState, useEffect, useMemo, useRef, useCallback } from 'react';
import {
  MdTrendingUp,
  MdTrendingDown,
  MdWarningAmber,
  MdSearch,
  MdClose,
  MdSearchOff,
  MdArrowUpward,
  MdArrowDownward,
  MdUnfoldMore,
} from 'react-icons/md';
import { useMarketData } from '@/hooks/useMarketData';
import type { KamisItemPrice, ImportantChange } from '@/types';
import MarketPriceSkeleton from './MarketPriceSkeleton';

// ── 가격 문자열 파싱 ───────────────────────────────────
function parseKamisPrice(raw: unknown): number | null {
  if (raw == null) return null;
  if (typeof raw === 'number') return raw;
  if (typeof raw !== 'string') return null;
  if (!raw || raw === '-' || raw.trim() === '') return null;
  const num = parseInt(raw.replace(/,/g, ''), 10);
  return Number.isNaN(num) ? null : num;
}

// ── 주요 변동 감지 임계값 ──────────────────────────────
const DAILY_CHANGE_THRESHOLD = 5;   // %
const WEEKLY_CHANGE_THRESHOLD = 10; // %

function detectImportantChanges(items: KamisItemPrice[]): ImportantChange[] {
  const changes: ImportantChange[] = [];

  for (const item of items) {
    const current = parseKamisPrice(item.dpr1);
    const yesterday = parseKamisPrice(item.dpr2);
    const weekAgo = parseKamisPrice(item.dpr3);

    if (current == null || current === 0) continue;

    let isImportant = false;
    let changePercent = 0;
    let previousPrice = 0;

    // 전일 대비 변동
    if (yesterday != null && yesterday !== 0) {
      changePercent = ((current - yesterday) / yesterday) * 100;
      previousPrice = yesterday;
      if (Math.abs(changePercent) >= DAILY_CHANGE_THRESHOLD) {
        isImportant = true;
      }
    }

    // 1주일 전 대비 변동
    if (!isImportant && weekAgo != null && weekAgo !== 0) {
      const weekChange = ((current - weekAgo) / weekAgo) * 100;
      if (Math.abs(weekChange) >= WEEKLY_CHANGE_THRESHOLD) {
        isImportant = true;
        changePercent = weekChange;
        previousPrice = weekAgo;
      }
    }

    if (isImportant) {
      changes.push({
        item_name: item.item_name,
        productno: item.productno,
        category_name: item.category_name,
        unit: item.unit,
        currentPrice: current,
        previousPrice,
        changePercent,
        direction: changePercent > 0 ? 'up' : 'down',
      });
    }
  }

  return changes.sort((a, b) => Math.abs(b.changePercent) - Math.abs(a.changePercent));
}

// "오이" / "오 이" / "OI" 등 가벼운 정규화 — 공백 제거 + 소문자
function normalizeQuery(value: string): string {
  return value.replace(/\s+/g, '').toLowerCase();
}

// ── 정렬 상태 ─────────────────────────────────────────
type SortKey = 'name' | 'current' | 'change';
type SortDir = 'asc' | 'desc';

// 헤더 클릭 사이클: 무정렬 → desc → asc → 무정렬 (백엔드 부류 코드 순으로 복귀)
function nextSortState(
  current: { key: SortKey; dir: SortDir } | null,
  clicked: SortKey,
): { key: SortKey; dir: SortDir } | null {
  if (!current || current.key !== clicked) return { key: clicked, dir: 'desc' };
  if (current.dir === 'desc') return { key: clicked, dir: 'asc' };
  return null;
}

function changePercent(item: KamisItemPrice): number | null {
  const cur = parseKamisPrice(item.dpr1);
  const prev = parseKamisPrice(item.dpr2);
  if (cur == null || prev == null || prev === 0) return null;
  return ((cur - prev) / prev) * 100;
}

export default function MarketPricePage() {
  const { latestPrices, loading, error, fetchLatest } = useMarketData();
  const [productCls, setProductCls] = useState<'' | '01' | '02'>('01');
  const [categoryFilter, setCategoryFilter] = useState<string>('');
  const [searchQuery, setSearchQuery] = useState<string>('');
  const [sortState, setSortState] = useState<{ key: SortKey; dir: SortDir } | null>(null);
  const searchInputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => { fetchLatest(productCls); }, [fetchLatest, productCls]);

  const importantChanges = useMemo(() => detectImportantChanges(latestPrices), [latestPrices]);

  // 부류별 카테고리 목록 추출
  const categories = useMemo(() => {
    const map = new Map<string, string>();
    for (const item of latestPrices) {
      if (item.category_code && item.category_name) {
        map.set(item.category_code, item.category_name);
      }
    }
    return Array.from(map.entries());
  }, [latestPrices]);

  // 필터링된 가격 목록 (부류 + 텍스트 검색)
  const filteredPrices = useMemo(() => {
    const byCategory = !categoryFilter
      ? latestPrices
      : latestPrices.filter(item => item.category_code === categoryFilter);
    const q = normalizeQuery(searchQuery);
    if (!q) return byCategory;
    return byCategory.filter(item => {
      const name = normalizeQuery(item.item_name ?? '');
      const cat = normalizeQuery(item.category_name ?? '');
      return name.includes(q) || cat.includes(q);
    });
  }, [latestPrices, categoryFilter, searchQuery]);

  // 정렬: 부류 + 검색을 통과한 결과에 사용자가 선택한 키로 안정 정렬을 적용.
  // null 값(가격 결측)은 항상 맨 뒤로 보내 농민이 "값 없는 줄"에 시선 뺏기지 않게 한다.
  const sortedPrices = useMemo(() => {
    if (!sortState) return filteredPrices;
    const sign = sortState.dir === 'asc' ? 1 : -1;
    const indexed = filteredPrices.map((item, idx) => ({ item, idx }));
    indexed.sort((a, b) => {
      let av: number | string | null = null;
      let bv: number | string | null = null;
      if (sortState.key === 'name') {
        av = a.item.item_name ?? '';
        bv = b.item.item_name ?? '';
      } else if (sortState.key === 'current') {
        av = parseKamisPrice(a.item.dpr1);
        bv = parseKamisPrice(b.item.dpr1);
      } else {
        av = changePercent(a.item);
        bv = changePercent(b.item);
      }
      const aMissing = av == null || av === '';
      const bMissing = bv == null || bv === '';
      if (aMissing && bMissing) return a.idx - b.idx;
      if (aMissing) return 1;
      if (bMissing) return -1;
      if (typeof av === 'string' && typeof bv === 'string') {
        const cmp = av.localeCompare(bv, 'ko');
        return cmp !== 0 ? sign * cmp : a.idx - b.idx;
      }
      const an = av as number;
      const bn = bv as number;
      if (an === bn) return a.idx - b.idx;
      return sign * (an - bn);
    });
    return indexed.map(x => x.item);
  }, [filteredPrices, sortState]);

  const toggleSort = useCallback((key: SortKey) => {
    setSortState(prev => nextSortState(prev, key));
  }, []);

  const isSearching = searchQuery.trim().length > 0;
  const hasNoResults = isSearching && sortedPrices.length === 0;
  const clearSearch = useCallback(() => {
    setSearchQuery('');
    searchInputRef.current?.blur();
  }, []);

  // "/" 단축키로 검색 포커스 (다른 입력 요소에 포커스가 있으면 무시).
  // ESC 로 검색 초기화. 한국어 IME 조합 중에는 isComposing 으로 보호.
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (event.isComposing) return;
      const target = event.target as HTMLElement | null;
      const tag = target?.tagName;
      const isTyping =
        tag === 'INPUT' ||
        tag === 'TEXTAREA' ||
        tag === 'SELECT' ||
        target?.isContentEditable === true;

      if (event.key === '/' && !event.metaKey && !event.ctrlKey && !event.altKey) {
        if (isTyping && target !== searchInputRef.current) return;
        event.preventDefault();
        searchInputRef.current?.focus();
        searchInputRef.current?.select();
        return;
      }

      if (event.key === 'Escape' && document.activeElement === searchInputRef.current) {
        if (searchQuery !== '') {
          event.preventDefault();
          clearSearch();
        }
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [searchQuery, clearSearch]);

  // ── 로딩 / 에러 ────────────────────────────────────────
  if (loading && latestPrices.length === 0) {
    return <MarketPriceSkeleton />;
  }

  if (error && latestPrices.length === 0) {
    return (
      <div className="card bg-[color:var(--color-danger-light)] border-[color:var(--color-danger-light)]">
        <div className="flex items-center gap-3">
          <MdWarningAmber className="text-2xl text-danger" />
          <div>
            <p className="font-semibold text-[color:var(--color-ink)]">시세 정보를 불러올 수 없습니다</p>
            <p className="text-sm text-[color:var(--color-ink-mute)] mt-1">{error}</p>
          </div>
        </div>
        <button onClick={() => fetchLatest(productCls)} className="btn-primary mt-4 text-sm">
          다시 시도
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* ── 소매/도매 토글 ─────────────────────────────── */}
      <div className="flex items-center gap-2">
        {([['01', '소매가격'], ['02', '도매가격']] as const).map(([code, label]) => (
          <button
            key={code}
            onClick={() => setProductCls(code)}
            className={`px-4 py-2 rounded-xl text-sm font-medium transition ${
              productCls === code
                ? 'bg-primary text-white'
                : 'bg-white text-[color:var(--color-ink-mute)] border border-[color:var(--color-line)] hover:bg-[color:var(--color-surface)]'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {/* ── 주요 가격 변동 알림 ──────────────────────── */}
      <div className={`card ${importantChanges.length > 0 ? 'bg-[color:var(--tint-warning)] border-[color:var(--color-accent)]/30' : 'bg-[color:var(--color-primary-soft)] border-[color:var(--color-primary-soft)]'}`}>
        <div className="flex items-center gap-2 mb-3">
          <MdWarningAmber className={`text-xl ${importantChanges.length > 0 ? 'text-[color:var(--color-accent)]' : 'text-success'}`} />
          <h3 className="section-title">주요 가격 변동</h3>
          <span className="text-xs text-[color:var(--color-ink-faint)] ml-auto">전일 대비 {DAILY_CHANGE_THRESHOLD}% 이상 또는 전주 대비 {WEEKLY_CHANGE_THRESHOLD}% 이상</span>
        </div>

        {importantChanges.length === 0 ? (
          <p className="text-sm text-[color:var(--color-ink-mute)]">오늘은 큰 가격 변동이 없습니다.</p>
        ) : (
          <div className="space-y-2">
            {importantChanges.slice(0, 10).map((c, i) => (
              <div key={i} className="flex items-center justify-between p-3 bg-white/70 rounded-xl">
                <div className="flex items-center gap-2">
                  {c.direction === 'up'
                    ? <MdTrendingUp className="text-lg text-danger" />
                    : <MdTrendingDown className="text-lg text-success" />}
                  <span className="font-medium text-[color:var(--color-ink)]">{c.item_name}</span>
                  <span className="text-xs text-[color:var(--color-ink-faint)]">{c.category_name}</span>
                </div>
                <div className="flex items-center gap-3">
                  <span className="text-sm text-[color:var(--color-ink-mute)]">
                    {c.previousPrice.toLocaleString()} → {c.currentPrice.toLocaleString()}원/{c.unit}
                  </span>
                  <span className={`badge ${c.direction === 'up' ? 'badge-danger' : 'badge-success'}`}>
                    {c.direction === 'up' ? '+' : ''}{c.changePercent.toFixed(1)}%
                  </span>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* ── 검색 바 ──────────────────────────────────── */}
      <div className="relative">
        <span className="pointer-events-none absolute inset-y-0 left-3 flex items-center text-[color:var(--color-ink-faint)]">
          <MdSearch className="text-xl" aria-hidden />
        </span>
        <input
          ref={searchInputRef}
          type="search"
          inputMode="search"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          placeholder="품목 또는 부류 검색 — 예: 오이, 채소"
          aria-label="품목 검색"
          className="w-full rounded-xl border border-[color:var(--color-line)] bg-white py-2.5 pl-10 pr-24 text-sm text-[color:var(--color-ink)] placeholder:text-[color:var(--color-ink-faint)] focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/30"
        />
        {isSearching ? (
          <button
            type="button"
            onClick={clearSearch}
            aria-label="검색 초기화"
            className="absolute inset-y-1 right-2 inline-flex items-center gap-1 rounded-lg px-2 text-xs font-medium text-[color:var(--color-ink-mute)] hover:bg-[color:var(--color-surface-deep)] hover:text-[color:var(--color-ink-soft)] focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/40"
          >
            <MdClose className="text-base" aria-hidden />
            <span className="hidden sm:inline">초기화</span>
            <kbd className="hidden rounded border border-[color:var(--color-line)] bg-[color:var(--color-surface)] px-1 text-[10px] font-mono text-[color:var(--color-ink-mute)] sm:inline">Esc</kbd>
          </button>
        ) : (
          <kbd
            aria-hidden
            className="pointer-events-none absolute inset-y-0 right-3 my-auto h-6 select-none items-center rounded border border-[color:var(--color-line)] bg-[color:var(--color-surface)] px-1.5 font-mono text-[11px] text-[color:var(--color-ink-mute)] hidden sm:inline-flex"
          >
            /
          </kbd>
        )}
      </div>
      <p
        role="status"
        aria-live="polite"
        className="-mt-3 text-xs text-[color:var(--color-ink-mute)]"
      >
        {isSearching
          ? `검색 결과: ${sortedPrices.length}개 품목`
          : `전체 ${sortedPrices.length}개 품목${categoryFilter ? ` (부류 필터 적용 중)` : ''}`}
      </p>

      {/* ── 부류 필터 ────────────────────────────────── */}
      <div className="flex flex-wrap items-center gap-2">
        <button
          onClick={() => setCategoryFilter('')}
          className={`px-3 py-1.5 rounded-lg text-sm transition ${
            !categoryFilter ? 'bg-primary text-white' : 'bg-white text-[color:var(--color-ink-mute)] border border-[color:var(--color-line)] hover:bg-[color:var(--color-surface)]'
          }`}
        >
          전체
        </button>
        {categories.map(([code, name]) => (
          <button
            key={code}
            onClick={() => setCategoryFilter(code)}
            className={`px-3 py-1.5 rounded-lg text-sm transition ${
              categoryFilter === code ? 'bg-primary text-white' : 'bg-white text-[color:var(--color-ink-mute)] border border-[color:var(--color-line)] hover:bg-[color:var(--color-surface)]'
            }`}
          >
            {name}
          </button>
        ))}
      </div>

      {/* ── 가격 목록 테이블 ─────────────────────────── */}
      <div className="card">
        <div className="flex items-center justify-between mb-4">
          <h3 className="section-title">
            {productCls === '01' ? '소매' : '도매'} 가격 현황
          </h3>
          <div className="flex items-center gap-3">
            {sortState && (
              <button
                type="button"
                onClick={() => setSortState(null)}
                className="text-[11px] font-medium text-[color:var(--color-ink-mute)] hover:text-[color:var(--color-ink-soft)] underline-offset-2 hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/40 rounded"
                aria-label="정렬 초기화"
              >
                정렬 초기화
              </button>
            )}
            <span className="text-xs text-[color:var(--color-ink-faint)]">{sortedPrices.length}개 품목</span>
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-[color:var(--color-line)]">
                <SortableHeader
                  label="품목"
                  align="left"
                  sortKey="name"
                  state={sortState}
                  onToggle={toggleSort}
                />
                <th className="text-center py-3 px-4 font-semibold text-[color:var(--color-ink-mute)]">단위</th>
                <SortableHeader
                  label="당일"
                  align="right"
                  sortKey="current"
                  state={sortState}
                  onToggle={toggleSort}
                />
                <th className="text-right py-3 px-4 font-semibold text-[color:var(--color-ink-mute)]">1일전</th>
                <th className="text-right py-3 px-4 font-semibold text-[color:var(--color-ink-mute)]">1주전</th>
                <th className="text-right py-3 px-4 font-semibold text-[color:var(--color-ink-mute)]">1개월전</th>
                <SortableHeader
                  label="등락"
                  align="center"
                  sortKey="change"
                  state={sortState}
                  onToggle={toggleSort}
                />
              </tr>
            </thead>
            <tbody>
              {sortedPrices.map((item, idx) => {
                const cur = parseKamisPrice(item.dpr1);
                const prev = parseKamisPrice(item.dpr2);
                const diff = cur != null && prev != null && prev !== 0
                  ? ((cur - prev) / prev * 100)
                  : null;

                return (
                  <tr key={idx} className="border-b border-gray-50 hover:bg-[color:var(--color-surface)] transition-colors">
                    <td className="py-3 px-4">
                      <span className="font-medium">{item.item_name}</span>
                    </td>
                    <td className="text-center py-3 px-4 text-[color:var(--color-ink-faint)]">{item.unit}</td>
                    <td className="text-right py-3 px-4 font-semibold">
                      {item.dpr1 || '-'}
                    </td>
                    <td className="text-right py-3 px-4 text-[color:var(--color-ink-mute)]">{item.dpr2 || '-'}</td>
                    <td className="text-right py-3 px-4 text-[color:var(--color-ink-mute)]">{item.dpr3 || '-'}</td>
                    <td className="text-right py-3 px-4 text-[color:var(--color-ink-mute)]">{item.dpr4 || '-'}</td>
                    <td className="text-center py-3 px-4">
                      {diff != null ? (
                        <span className={`inline-flex items-center gap-0.5 text-xs font-medium ${
                          diff > 0 ? 'text-danger' : diff < 0 ? 'text-success' : 'text-[color:var(--color-ink-faint)]'
                        }`}>
                          {diff > 0 ? <MdTrendingUp /> : diff < 0 ? <MdTrendingDown /> : null}
                          {diff > 0 ? '+' : ''}{diff.toFixed(1)}%
                        </span>
                      ) : (
                        <span className="text-[color:var(--color-ink-disabled)]">-</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {sortedPrices.length === 0 && !loading && (
          hasNoResults ? (
            <div className="flex flex-col items-center justify-center gap-3 py-10 text-center">
              <MdSearchOff className="text-4xl text-[color:var(--color-ink-disabled)]" aria-hidden />
              <div>
                <p className="text-sm font-medium text-[color:var(--color-ink-soft)]">"{searchQuery.trim()}" 와 일치하는 품목이 없습니다.</p>
                <p className="mt-1 text-xs text-[color:var(--color-ink-faint)]">부류 필터를 해제하거나 다른 키워드로 검색해보세요.</p>
              </div>
              <button
                type="button"
                onClick={clearSearch}
                className="rounded-lg border border-[color:var(--color-line)] px-3 py-1.5 text-xs font-medium text-[color:var(--color-ink-mute)] hover:bg-[color:var(--color-surface)] focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/40"
              >
                검색 초기화
              </button>
            </div>
          ) : (
            <p className="text-sm text-[color:var(--color-ink-faint)] text-center py-8">가격 데이터가 없습니다.</p>
          )
        )}
      </div>
    </div>
  );
}

// ── 정렬 가능한 컬럼 헤더 ─────────────────────────────
function SortableHeader({
  label,
  align,
  sortKey,
  state,
  onToggle,
}: {
  label: string;
  align: 'left' | 'right' | 'center';
  sortKey: SortKey;
  state: { key: SortKey; dir: SortDir } | null;
  onToggle: (key: SortKey) => void;
}) {
  const active = state?.key === sortKey;
  const dir = active ? state!.dir : undefined;
  const ariaSort: 'ascending' | 'descending' | 'none' =
    !active ? 'none' : dir === 'asc' ? 'ascending' : 'descending';
  const alignClass =
    align === 'left' ? 'text-left' : align === 'right' ? 'text-right' : 'text-center';
  const justifyClass =
    align === 'left' ? 'justify-start' : align === 'right' ? 'justify-end' : 'justify-center';
  const Icon = !active ? MdUnfoldMore : dir === 'asc' ? MdArrowUpward : MdArrowDownward;
  const dirLabel = !active ? '정렬 안 됨' : dir === 'asc' ? '오름차순' : '내림차순';

  return (
    <th
      scope="col"
      aria-sort={ariaSort}
      className={`${alignClass} py-3 px-4 font-semibold text-[color:var(--color-ink-mute)]`}
    >
      <button
        type="button"
        onClick={() => onToggle(sortKey)}
        aria-label={`${label} 정렬 (현재 ${dirLabel})`}
        className={`inline-flex w-full items-center gap-1 ${justifyClass} rounded px-1 py-0.5 transition hover:text-[color:var(--color-ink)] focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/40 ${
          active ? 'text-primary' : ''
        }`}
      >
        <span>{label}</span>
        <Icon
          className={`text-base ${active ? 'opacity-100' : 'opacity-40'}`}
          aria-hidden
        />
      </button>
    </th>
  );
}
