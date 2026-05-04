// Design Ref: §6.1 — ReviewsPage (Mock → API 연동 전환)
import { useState, useEffect } from 'react';
import { PieChart, Pie, Cell, BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, Legend } from 'recharts';
import { MdTrendingUp, MdStar, MdPlayArrow, MdSettings, MdDownload, MdWarning, MdStorage } from 'react-icons/md';
import { useReviewAnalysis } from '@/hooks/useReviewAnalysis';
import RAGSearchPanel from './RAGSearchPanel';
import AnalysisSettingsModal from './AnalysisSettingsModal';
import { REVIEWS, SENTIMENT_SUMMARY, KEYWORD_DATA, WEEKLY_TRENDS, AI_STRATEGIES } from '@/mocks/reviews';
import { Spinner, StatusDot } from '@/components/ui';
import { cn } from '@/lib/cn';

const SENTIMENT_COLORS = { positive: 'var(--color-success)', negative: 'var(--color-danger)', neutral: 'var(--color-ink-faint)' };

export default function ReviewsPage() {
  const [selectedPlatform, setSelectedPlatform] = useState<string>('all');
  const [selectedSentiment, setSelectedSentiment] = useState<string>('all');
  const [mounted, setMounted] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  useEffect(() => { setMounted(true); }, []);

  const {
    analysis, isAnalyzing, isEmbedding,
    embedProgress, analyzeProgress, progressMessage,
    error, analyzeReviews, searchResults, isSearching, searchReviews,
    trends, anomalies, downloadReport, embedReviews,
    settings, updateSettings,
  } = useReviewAnalysis();

  const sentimentSummary = analysis?.sentiment_summary || SENTIMENT_SUMMARY;
  const keywords = analysis?.keywords || KEYWORD_DATA;
  const weeklyTrends = trends.length > 0
    ? trends.map(t => ({ week: t.week, positive: t.positive, negative: t.negative, neutral: t.neutral }))
    : WEEKLY_TRENDS;
  const summary = analysis?.summary;
  const strategies = summary?.suggestions
    ? summary.suggestions.map((s, i) => ({ id: `sug-${i}`, title: s, description: '', priority: '중간' as const }))
    : AI_STRATEGIES;

  const filteredReviews = REVIEWS.filter(r => {
    if (selectedPlatform !== 'all' && r.platform !== selectedPlatform) return false;
    if (selectedSentiment !== 'all' && r.sentiment !== selectedSentiment) return false;
    return true;
  });

  const avgRating = REVIEWS.length > 0
    ? (REVIEWS.reduce((sum, r) => sum + r.rating, 0) / REVIEWS.length).toFixed(1)
    : '0';

  const pieData = [
    { name: '긍정', value: sentimentSummary.positive, color: SENTIMENT_COLORS.positive },
    { name: '부정', value: sentimentSummary.negative, color: SENTIMENT_COLORS.negative },
    { name: '중립', value: sentimentSummary.neutral, color: SENTIMENT_COLORS.neutral },
  ];

  const hasAnalysis = !!analysis;

  return (
    <div className="space-y-6">
      {/* Action Bar */}
      <div className="flex flex-wrap items-center gap-2">
        <button
          onClick={() => embedReviews()}
          disabled={isEmbedding}
          aria-busy={isEmbedding}
          className="btn-outline relative overflow-hidden !py-2 !text-[14px]"
        >
          {isEmbedding && (
            <span
              aria-hidden
              className="absolute left-0 top-0 h-full bg-[color:var(--color-info)]/15 transition-all duration-300"
              style={{ width: `${embedProgress}%` }}
            />
          )}
          {isEmbedding ? <Spinner size={16} tone="mute" label="" /> : <MdStorage aria-hidden className="text-base relative z-10" />}
          <span className="relative z-10">
            {isEmbedding ? `임베딩 ${embedProgress}%` : '임베딩 저장'}
          </span>
        </button>
        <button
          onClick={() => analyzeReviews('all', settings.default_batch_size)}
          disabled={isAnalyzing}
          aria-busy={isAnalyzing}
          className="btn-primary relative overflow-hidden !py-2 !text-[14px]"
        >
          {isAnalyzing && (
            <span
              aria-hidden
              className="absolute left-0 top-0 h-full bg-white/20 transition-all duration-300"
              style={{ width: `${analyzeProgress}%` }}
            />
          )}
          {isAnalyzing ? <Spinner size={16} tone="inverse" label="" /> : <MdPlayArrow aria-hidden className="text-base relative z-10" />}
          <span className="relative z-10">
            {isAnalyzing ? `분석 ${analyzeProgress}%` : 'AI 분석 실행'}
          </span>
        </button>
        {hasAnalysis && (
          <button onClick={downloadReport} className="btn-outline !py-2 !text-[14px]">
            <MdDownload aria-hidden className="text-base" /> PDF 리포트
          </button>
        )}
        <button
          onClick={() => setShowSettings(true)}
          className="icon-btn ml-auto"
          aria-label="분석 설정"
        >
          <MdSettings aria-hidden className="text-[19px]" />
        </button>
      </div>

      {progressMessage && (isEmbedding || isAnalyzing) && (
        <div role="status" aria-live="polite" className="flex items-center gap-2 rounded-xl border border-[color:var(--color-info)]/20 bg-[color:var(--tint-info)] px-4 py-3 text-[14px] text-[color:var(--color-info)]">
          <Spinner size={16} tone="mute" label="" />
          {progressMessage}
        </div>
      )}

      {error && (
        <div role="alert" className="rounded-xl border border-[color:var(--color-danger-light)] bg-[color:var(--color-danger-light)]/40 px-4 py-3 text-[14px] text-[color:var(--color-danger)]">
          {error}
        </div>
      )}

      {/* Anomaly Alerts */}
      {anomalies.length > 0 && (
        <ul className="space-y-2">
          {anomalies.map((a, i) => (
            <li
              key={i}
              role="alert"
              className="flex items-center gap-2 rounded-xl border border-[color:var(--color-danger-light)] bg-[color:var(--color-danger-light)]/40 px-3.5 py-2.5"
            >
              <MdWarning aria-hidden className="flex-shrink-0 text-[18px] text-[color:var(--color-danger)]" />
              <span className="text-[13.5px] text-[color:var(--color-danger)]">{a.message}</span>
            </li>
          ))}
        </ul>
      )}

      {/* Analysis Meta */}
      {hasAnalysis && (
        <div className="flex flex-wrap gap-3 text-[12px] text-[color:var(--color-ink-faint)]">
          <span>Provider: {analysis.llm_provider}</span>
          <span aria-hidden>·</span>
          <span>Model: {analysis.llm_model}</span>
          <span aria-hidden>·</span>
          <span className="num">{analysis.processing_time_ms}ms</span>
        </div>
      )}

      {/* Summary Cards */}
      <dl className="grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-4">
        <div className="card !p-3 text-center sm:!p-4">
          <dt className="text-[12.5px] text-[color:var(--color-ink-mute)] sm:text-[13.5px]">총 리뷰</dt>
          <dd className="num text-[1.6rem] font-bold text-[color:var(--color-ink)] sm:text-[1.85rem]">{sentimentSummary.total}</dd>
        </div>
        <div className="card !p-3 text-center sm:!p-4">
          <dt className="text-[12.5px] text-[color:var(--color-ink-mute)] sm:text-[13.5px]">긍정률</dt>
          <dd className="num text-[1.6rem] font-bold text-[color:var(--color-success)] sm:text-[1.85rem]">
            {sentimentSummary.total > 0 ? Math.round(sentimentSummary.positive / sentimentSummary.total * 100) : 0}%
          </dd>
        </div>
        <div className="card !p-3 text-center sm:!p-4">
          <dt className="text-[12.5px] text-[color:var(--color-ink-mute)] sm:text-[13.5px]">평균 평점</dt>
          <dd className="num inline-flex items-center justify-center gap-1 text-[1.6rem] font-bold text-[color:var(--color-accent-dark)] sm:text-[1.85rem]">
            {avgRating} <MdStar aria-hidden className="text-[color:var(--color-accent)]" />
          </dd>
        </div>
        <div className="card !p-3 text-center sm:!p-4">
          <dt className="text-[12.5px] text-[color:var(--color-ink-mute)] sm:text-[13.5px]">AI 인사이트</dt>
          <dd className="num text-[1.6rem] font-bold text-[color:var(--color-primary-dark)] sm:text-[1.85rem]">{strategies.length}건</dd>
        </div>
      </dl>

      {/* AI Summary (from LLM) */}
      {summary?.overall && (
        <section aria-labelledby="ai-summary" className="card border-l-4 !border-l-[color:var(--color-primary)]">
          <h3 id="ai-summary" className="section-title mb-2">AI 분석 요약</h3>
          <p className="text-[14px] leading-[1.7] text-[color:var(--color-ink-soft)]">{summary.overall}</p>
          {summary.positives?.length > 0 && (
            <ul className="mt-2 flex flex-wrap gap-1">
              {summary.positives.map((p, i) => (
                <li key={i} className="badge-success text-[12px]">+ {p}</li>
              ))}
            </ul>
          )}
          {summary.negatives?.length > 0 && (
            <ul className="mt-1 flex flex-wrap gap-1">
              {summary.negatives.map((n, i) => (
                <li key={i} className="badge-danger text-[12px]">- {n}</li>
              ))}
            </ul>
          )}
        </section>
      )}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        {/* Sentiment Pie Chart */}
        <section aria-labelledby="sentiment-chart" className="card">
          <h3 id="sentiment-chart" className="section-title mb-4">감성 분석</h3>
          {mounted && (
            <div className="h-[260px] overflow-hidden sm:h-[300px]">
              <ResponsiveContainer width="100%" height="100%" debounce={50}>
                <PieChart>
                  <Pie data={pieData} cx="50%" cy="45%" innerRadius={55} outerRadius={90} paddingAngle={3} dataKey="value">
                    {pieData.map((entry, i) => <Cell key={i} fill={entry.color} />)}
                  </Pie>
                  <Tooltip formatter={(value) => `${value}건`} />
                  <Legend formatter={(value, entry: any) => `${value} ${entry.payload.value}건`} iconType="circle" iconSize={10} />
                </PieChart>
              </ResponsiveContainer>
            </div>
          )}
        </section>

        {/* Weekly Trend */}
        <section aria-labelledby="weekly-chart" className="card">
          <h3 id="weekly-chart" className="section-title mb-4">주간 추이</h3>
          {mounted && (
            <div className="h-[200px] overflow-hidden sm:h-[250px]">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={weeklyTrends}>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--color-line)" />
                  <XAxis dataKey="week" tick={{ fontSize: 12 }} />
                  <YAxis tick={{ fontSize: 12 }} />
                  <Tooltip />
                  <Bar dataKey="positive" fill="var(--color-success)" name="긍정" radius={[4, 4, 0, 0]} />
                  <Bar dataKey="negative" fill="var(--color-danger)" name="부정" radius={[4, 4, 0, 0]} />
                  <Bar dataKey="neutral" fill="var(--color-ink-faint)" name="중립" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </section>
      </div>

      {/* Keyword Cloud */}
      <section aria-labelledby="keyword-section" className="card">
        <h3 id="keyword-section" className="section-title mb-4">키워드 분석</h3>
        <ul className="flex flex-wrap gap-2">
          {keywords.map(k => (
            <li
              key={k.word}
              className={cn(
                'rounded-full px-3 py-1.5 font-medium',
                k.sentiment === 'positive' && 'bg-[color:var(--color-primary-soft)] text-[color:var(--color-primary-dark)]',
                k.sentiment === 'negative' && 'bg-[color:var(--color-danger-light)] text-[color:var(--color-danger)]',
                k.sentiment === 'neutral' && 'bg-[color:var(--color-surface-deep)] text-[color:var(--color-ink-soft)]',
              )}
              style={{ fontSize: `${Math.max(13, Math.min(20, 11 + k.count))}px` }}
            >
              {k.word} <span className="num opacity-70">({k.count})</span>
            </li>
          ))}
        </ul>
      </section>

      {/* RAG Search */}
      <RAGSearchPanel onSearch={searchReviews} results={searchResults} isSearching={isSearching} />

      {/* AI Strategy Recommendations */}
      <section aria-labelledby="strategy-section" className="card">
        <div className="mb-4 flex items-center gap-2">
          <MdTrendingUp aria-hidden className="text-[20px] text-[color:var(--color-primary)]" />
          <h3 id="strategy-section" className="section-title">AI 판매 전략 추천</h3>
        </div>
        <ul className="space-y-3">
          {strategies.map(s => (
            <li key={s.id} className="rounded-xl border border-[color:var(--color-line)] p-4 transition-colors hover:border-[color:var(--color-primary-light)]">
              <div className="flex items-start justify-between gap-3">
                <h4 className="text-[15px] font-bold text-[color:var(--color-ink)]">{s.title}</h4>
                <span
                  className={cn(
                    'badge text-[12px]',
                    s.priority === '높음' && 'badge-danger',
                    s.priority === '중간' && 'badge-warning',
                    s.priority !== '높음' && s.priority !== '중간' && 'badge-info',
                  )}
                >
                  {s.priority}
                </span>
              </div>
              {s.description && <p className="mt-2 text-[13.5px] leading-[1.6] text-[color:var(--color-ink-mute)]">{s.description}</p>}
            </li>
          ))}
        </ul>
      </section>

      {/* Review List */}
      <section aria-labelledby="review-list" className="card">
        <div className="mb-4">
          <h3 id="review-list" className="section-title mb-3">리뷰 목록</h3>
          <div className="flex flex-wrap items-center gap-2">
            {['all', '네이버스마트스토어', '쿠팡'].map(p => (
              <button
                key={p}
                onClick={() => setSelectedPlatform(p)}
                aria-pressed={selectedPlatform === p}
                className={cn('chip cursor-pointer', selectedPlatform === p && 'chip-active')}
              >
                {p === 'all' ? '전체' : p}
              </button>
            ))}
            <select
              value={selectedSentiment}
              onChange={(e) => setSelectedSentiment(e.target.value)}
              className="select ml-auto !min-h-[34px] !w-auto !py-1.5 !pr-8 !text-[13px]"
              aria-label="감성으로 필터링"
            >
              <option value="all">감성 전체</option>
              <option value="positive">긍정</option>
              <option value="negative">부정</option>
              <option value="neutral">중립</option>
            </select>
          </div>
        </div>
        <ul className="max-h-[320px] space-y-2 overflow-y-auto sm:max-h-[400px]">
          {filteredReviews.map((r) => (
            <li key={r.id} className="flex items-start gap-3 rounded-xl bg-[color:var(--color-surface)] p-3">
              <StatusDot
                tone={r.sentiment === 'positive' ? 'success' : r.sentiment === 'negative' ? 'danger' : 'mute'}
                size={10}
                className="mt-1"
                label={r.sentiment === 'positive' ? '긍정' : r.sentiment === 'negative' ? '부정' : '중립'}
              />
              <div className="min-w-0 flex-1">
                <p className="text-[13.5px] leading-[1.55] text-[color:var(--color-ink-soft)]">{r.text}</p>
                <div className="mt-1 flex flex-wrap items-center gap-2 text-[12px]">
                  <span className="text-[color:var(--color-ink-faint)]">{r.platform}</span>
                  <span aria-label={`평점 ${r.rating}점`} className="text-[color:var(--color-accent)]">{'★'.repeat(r.rating)}</span>
                  <time className="num text-[color:var(--color-ink-faint)]">{r.date}</time>
                </div>
              </div>
            </li>
          ))}
        </ul>
      </section>

      {/* Settings Modal */}
      <AnalysisSettingsModal
        isOpen={showSettings}
        onClose={() => setShowSettings(false)}
        settings={settings}
        onSave={updateSettings}
      />
    </div>
  );
}
