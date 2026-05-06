// Design Ref: §6.1 — ReviewsPage (Mock → API 연동 전환)
// ts/06-reviews-pipeline-state-analysis.md §5 기준: mock 폴백 제거, 빈 상태는 placeholder.
import { useState, useEffect } from 'react';
import { PieChart, Pie, Cell, BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, Legend } from 'recharts';
import { MdTrendingUp, MdPlayArrow, MdSettings, MdDownload, MdWarning, MdStorage, MdInfoOutline, MdCheckCircle } from 'react-icons/md';
import { useReviewAnalysis } from '@/hooks/useReviewAnalysis';
import RAGSearchPanel from './RAGSearchPanel';
import AnalysisSettingsModal from './AnalysisSettingsModal';
import { Spinner } from '@/components/ui';
import { cn } from '@/lib/cn';

function EmptyChart({ message }: { message: string }) {
  return (
    <div className="flex h-[200px] items-center justify-center sm:h-[260px]">
      <p className="text-[13px] text-[color:var(--color-ink-faint)]">{message}</p>
    </div>
  );
}

function EmptyInline({ message }: { message: string }) {
  return (
    <p className="rounded-lg bg-[color:var(--color-surface)] px-3 py-4 text-center text-[13px] text-[color:var(--color-ink-faint)]">
      {message}
    </p>
  );
}

const SENTIMENT_COLORS = { positive: 'var(--color-success)', negative: 'var(--color-danger)', neutral: 'var(--color-ink-faint)' };

const EMPTY_SENTIMENT = { positive: 0, negative: 0, neutral: 0, total: 0 };

export default function ReviewsPage() {
  const [mounted, setMounted] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  useEffect(() => { setMounted(true); }, []);

  const {
    analysis, isAnalyzing, isEmbedding,
    embedProgress, analyzeProgress, progressMessage, notice,
    error, analyzeReviews, searchResults, isSearching, searchReviews,
    trends, anomalies, downloadReport, embedReviews,
    settings, updateSettings,
  } = useReviewAnalysis();

  const sentimentSummary = analysis?.sentiment_summary ?? EMPTY_SENTIMENT;
  const keywords = analysis?.keywords ?? [];
  const weeklyTrends = trends.length > 0
    ? trends.map(t => ({ week: t.week, positive: t.positive, negative: t.negative, neutral: t.neutral }))
    : [];
  const summary = analysis?.summary;
  const strategies = summary?.suggestions
    ? summary.suggestions.map((s, i) => ({ id: `sug-${i}`, title: s, description: '', priority: '중간' as const }))
    : [];

  const pieData = [
    { name: '긍정', value: sentimentSummary.positive, color: SENTIMENT_COLORS.positive },
    { name: '부정', value: sentimentSummary.negative, color: SENTIMENT_COLORS.negative },
    { name: '중립', value: sentimentSummary.neutral, color: SENTIMENT_COLORS.neutral },
  ];

  const hasAnalysis = !!analysis;
  const hasSentimentData = sentimentSummary.total > 0;
  const hasKeywords = keywords.length > 0;
  const hasTrendData = weeklyTrends.length > 0;
  const hasStrategies = strategies.length > 0;

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
          onClick={() => analyzeReviews(settings.default_batch_size)}
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

      {/* 완료/이미 처리됨 알림 — 4초 후 자동 소거 (즉시 종료된 SSE 도 사용자에게 보이도록) */}
      {notice && !isEmbedding && !isAnalyzing && (
        <div role="status" aria-live="polite" className="flex items-center gap-2 rounded-xl border border-[color:var(--color-success)]/30 bg-[color:var(--color-success)]/10 px-4 py-3 text-[14px] text-[color:var(--color-success)]">
          <MdCheckCircle aria-hidden className="text-[18px]" />
          {notice}
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

      {/* Summary Cards — 분석 데이터 없으면 placeholder. 평균 평점은 백엔드 API에 미노출이라 분석 전에는 '-' */}
      <dl className="grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-4">
        <div className="card !p-3 text-center sm:!p-4">
          <dt className="text-[12.5px] text-[color:var(--color-ink-mute)] sm:text-[13.5px]">분석된 리뷰</dt>
          <dd className="num text-[1.6rem] font-bold text-[color:var(--color-ink)] sm:text-[1.85rem]">
            {hasSentimentData ? sentimentSummary.total : '-'}
          </dd>
        </div>
        <div className="card !p-3 text-center sm:!p-4">
          <dt className="text-[12.5px] text-[color:var(--color-ink-mute)] sm:text-[13.5px]">긍정률</dt>
          <dd className="num text-[1.6rem] font-bold text-[color:var(--color-success)] sm:text-[1.85rem]">
            {hasSentimentData ? `${Math.round(sentimentSummary.positive / sentimentSummary.total * 100)}%` : '-'}
          </dd>
        </div>
        <div className="card !p-3 text-center sm:!p-4">
          <dt className="text-[12.5px] text-[color:var(--color-ink-mute)] sm:text-[13.5px]">부정률</dt>
          <dd className="num text-[1.6rem] font-bold text-[color:var(--color-danger)] sm:text-[1.85rem]">
            {hasSentimentData ? `${Math.round(sentimentSummary.negative / sentimentSummary.total * 100)}%` : '-'}
          </dd>
        </div>
        <div className="card !p-3 text-center sm:!p-4">
          <dt className="text-[12.5px] text-[color:var(--color-ink-mute)] sm:text-[13.5px]">AI 인사이트</dt>
          <dd className="num text-[1.6rem] font-bold text-[color:var(--color-primary-dark)] sm:text-[1.85rem]">
            {hasStrategies ? `${strategies.length}건` : '-'}
          </dd>
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
          {mounted && hasSentimentData ? (
            <div className="h-[260px] overflow-hidden sm:h-[300px]">
              <ResponsiveContainer width="100%" height="100%" debounce={50}>
                <PieChart>
                  <Pie data={pieData} cx="50%" cy="45%" innerRadius={55} outerRadius={90} paddingAngle={3} dataKey="value">
                    {pieData.map((entry, i) => <Cell key={i} fill={entry.color} />)}
                  </Pie>
                  <Tooltip formatter={(value) => `${value}건`} />
                  <Legend formatter={(value, entry) => `${value} ${(entry?.payload as { value?: number } | undefined)?.value ?? 0}건`} iconType="circle" iconSize={10} />
                </PieChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <EmptyChart message="AI 분석 실행 후 감성 분포가 표시됩니다." />
          )}
        </section>

        {/* Weekly Trend */}
        <section aria-labelledby="weekly-chart" className="card">
          <h3 id="weekly-chart" className="section-title mb-4">주간 추이</h3>
          {mounted && hasTrendData ? (
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
          ) : (
            <EmptyChart message="주간 트렌드는 분석 후 누적되어 표시됩니다." />
          )}
        </section>
      </div>

      {/* Keyword Cloud */}
      <section aria-labelledby="keyword-section" className="card">
        <h3 id="keyword-section" className="section-title mb-4">키워드 분석</h3>
        {hasKeywords ? (
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
        ) : (
          <EmptyInline message="키워드는 AI 분석 실행 후 추출됩니다." />
        )}
      </section>

      {/* RAG Search */}
      <RAGSearchPanel onSearch={searchReviews} results={searchResults} isSearching={isSearching} />

      {/* AI Strategy Recommendations */}
      <section aria-labelledby="strategy-section" className="card">
        <div className="mb-4 flex items-center gap-2">
          <MdTrendingUp aria-hidden className="text-[20px] text-[color:var(--color-primary)]" />
          <h3 id="strategy-section" className="section-title">AI 판매 전략 추천</h3>
        </div>
        {hasStrategies ? (
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
        ) : (
          <EmptyInline message="LLM 요약이 완료되면 판매 전략 제안이 표시됩니다." />
        )}
      </section>

      {/* Review List — 백엔드 /list 엔드포인트 미존재로 placeholder.
         적재된 리뷰를 보려면 위 RAG 의미 검색을 사용 (ts/06-reviews-pipeline-state-analysis.md §5 b-1). */}
      <section aria-labelledby="review-list" className="card">
        <h3 id="review-list" className="section-title mb-3">리뷰 목록</h3>
        <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed border-[color:var(--color-line)] bg-[color:var(--color-surface)] px-4 py-8 text-center">
          <MdInfoOutline aria-hidden className="text-[28px] text-[color:var(--color-ink-faint)]" />
          <p className="text-[14px] font-medium text-[color:var(--color-ink-soft)]">리뷰 목록 API 준비 중</p>
          <p className="max-w-md text-[12.5px] leading-[1.6] text-[color:var(--color-ink-faint)]">
            적재된 리뷰는 위의 <strong>의미 검색</strong>으로 자연어 조회가 가능합니다.
            전체 목록 조회 API는 추후 제공될 예정입니다.
          </p>
        </div>
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
