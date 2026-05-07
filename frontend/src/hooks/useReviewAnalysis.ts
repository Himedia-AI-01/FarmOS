// Design Ref: §6.3 — useReviewAnalysis Hook
import { useState, useEffect, useCallback, useRef } from 'react';
import type {
  AnalysisResult, SearchResult, TrendData, AnomalyAlert, AnalysisSettings,
  ReviewListItem, ReviewListResponse,
} from '@/types';

const API_BASE = '/api/v1/reviews';

async function apiFetch<T>(url: string, options?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    ...options,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `API Error ${res.status}`);
  }
  return res.json();
}

export function useReviewAnalysis() {
  const [analysis, setAnalysis] = useState<AnalysisResult | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [isEmbedding, setIsEmbedding] = useState(false);
  const [embedProgress, setEmbedProgress] = useState(0);
  const [analyzeProgress, setAnalyzeProgress] = useState(0);
  const [progressMessage, setProgressMessage] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [searchResults, setSearchResults] = useState<SearchResult[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  const [trends, setTrends] = useState<TrendData[]>([]);
  const [anomalies, setAnomalies] = useState<AnomalyAlert[]>([]);
  // 완료 알림 — SSE 가 즉시 끝나는 경우(이미 임베딩됨 등)에도 사용자에게 결과를 보여주려고 별도 보관.
  const [notice, setNotice] = useState<string | null>(null);
  const [settings, setSettings] = useState<AnalysisSettings>({
    auto_batch_enabled: false,
    batch_trigger_count: 10,
    batch_schedule: null,
    default_batch_size: 50,
  });

  // 리뷰 목록 (shop_reviews 페이지네이션) — RAG 검색과 별개
  const [reviewList, setReviewList] = useState<ReviewListItem[]>([]);
  const [reviewListTotal, setReviewListTotal] = useState(0);
  const [reviewListPage, setReviewListPage] = useState(1);
  const [reviewListPageSize, setReviewListPageSize] = useState(10);
  const [reviewListHasMore, setReviewListHasMore] = useState(false);
  const [isReviewListLoading, setIsReviewListLoading] = useState(false);
  const [reviewListRatingFilter, setReviewListRatingFilter] = useState<{ min: number | null; max: number | null }>({ min: null, max: null });

  // 활성 EventSource 추적 — 컴포넌트 언마운트 시 close 보장 (메모리 누수 + setState-after-unmount 방지)
  const analyzeEsRef = useRef<EventSource | null>(null);
  const embedEsRef = useRef<EventSource | null>(null);
  const mountedRef = useRef(true);
  const noticeTimerRef = useRef<number | null>(null);

  // notice 자동 소거 — 4초 후 사라짐. 새 알림이 오면 이전 타이머 취소.
  const showNotice = useCallback((message: string) => {
    if (noticeTimerRef.current !== null) {
      window.clearTimeout(noticeTimerRef.current);
    }
    setNotice(message);
    noticeTimerRef.current = window.setTimeout(() => {
      if (mountedRef.current) setNotice(null);
      noticeTimerRef.current = null;
    }, 4000);
  }, []);

  // 최신 분석 결과 조회 (초기 로드 시 실패해도 에러 표시 안함 — Mock 폴백)
  const fetchAnalysis = useCallback(async () => {
    setIsLoading(true);
    try {
      const data = await apiFetch<AnalysisResult>(`${API_BASE}/analysis`);
      setAnalysis(data);
    } catch {
      // 미로그인/404/네트워크 에러 시 Mock 폴백 (에러 표시 안함)
    } finally {
      setIsLoading(false);
    }
  }, []);

  // 분석 실행 (SSE 스트림으로 진행률 표시)
  const analyzeReviews = useCallback(async (batchSize = 50, sampleSize = 200) => {
    setIsAnalyzing(true);
    setAnalyzeProgress(0);
    setProgressMessage('분석 준비 중...');
    setError(null);
    // 직전 호출이 남긴 EventSource가 있으면 닫고 시작 (재호출 시 leak 방지)
    analyzeEsRef.current?.close();
    try {
      const es = new EventSource(`${API_BASE}/analyze/stream?batch_size=${batchSize}&sample_size=${sampleSize}`);
      analyzeEsRef.current = es;
      await new Promise<void>((resolve, reject) => {
        es.onmessage = (event) => {
          const data = JSON.parse(event.data);
          if (mountedRef.current) {
            setAnalyzeProgress(data.progress || 0);
            setProgressMessage(data.message || '');
          }
          // 에러 우선 처리 — backend가 progress=100 + error 형태로 보낼 수 있어
          // 분기를 분리하면 "분석 완료" notice 와 error 토스트가 동시에 표시되는 모순이 생긴다.
          if (data.error) {
            es.close();
            analyzeEsRef.current = null;
            if (mountedRef.current) setError(data.error);
            reject(new Error(data.error));
            return;
          }
          if (data.progress >= 100) {
            es.close();
            analyzeEsRef.current = null;
            if (mountedRef.current) {
              // DB 저장 후의 100% 메시지이므로 fetchAnalysis 가 새 record 를 가져온다.
              // (race-condition fix 는 backend api/review_analysis.py 참조)
              fetchAnalysis();
              showNotice(data.message || '분석 완료');
            }
            resolve();
          }
        };
        es.onerror = () => {
          es.close();
          analyzeEsRef.current = null;
          if (mountedRef.current) setError('분석 스트림 연결 실패');
          reject(new Error('SSE failed'));
        };
      });
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      if (mountedRef.current && msg !== 'SSE failed') setError(msg);
    } finally {
      if (mountedRef.current) {
        setIsAnalyzing(false);
        setAnalyzeProgress(0);
        setProgressMessage('');
      }
    }
  }, [fetchAnalysis, showNotice]);

  // RAG 의미 검색
  const searchReviews = useCallback(async (
    query: string,
    topK = 10,
    filters?: { platform?: string; rating_min?: number; rating_max?: number },
  ) => {
    setIsSearching(true);
    try {
      const data = await apiFetch<{ results: SearchResult[]; total: number }>(`${API_BASE}/search`, {
        method: 'POST',
        body: JSON.stringify({ query, top_k: topK, filters: filters || null }),
      });
      setSearchResults(data.results);
      return data.results;
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg);
      return [];
    } finally {
      setIsSearching(false);
    }
  }, []);

  // 트렌드 조회
  const fetchTrends = useCallback(async () => {
    try {
      const data = await apiFetch<{ trends: TrendData[]; anomalies: AnomalyAlert[] }>(`${API_BASE}/trends`);
      setTrends(data.trends);
      setAnomalies(data.anomalies);
    } catch {
      // 트렌드 없으면 무시
    }
  }, []);

  // PDF 다운로드
  const downloadReport = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/report/pdf`, { credentials: 'include' });
      if (!res.ok) throw new Error('PDF 다운로드 실패');
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'review-analysis-report.pdf';
      a.click();
      URL.revokeObjectURL(url);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg);
    }
  }, []);

  // 임베딩 (SSE 스트림으로 진행률 표시)
  const embedReviews = useCallback(async () => {
    setIsEmbedding(true);
    setEmbedProgress(0);
    setProgressMessage('임베딩 준비 중...');
    setError(null);
    embedEsRef.current?.close();
    try {
      const es = new EventSource(`${API_BASE}/embed/stream`);
      embedEsRef.current = es;
      await new Promise<void>((resolve, reject) => {
        es.onmessage = (event) => {
          const data = JSON.parse(event.data);
          if (mountedRef.current) {
            setEmbedProgress(data.progress || 0);
            setProgressMessage(data.message || '');
          }
          if (data.progress >= 100) {
            es.close();
            embedEsRef.current = null;
            if (mountedRef.current) {
              showNotice(data.message || '임베딩 완료');
            }
            resolve();
          }
        };
        es.onerror = () => {
          es.close();
          embedEsRef.current = null;
          if (mountedRef.current) setError('임베딩 스트림 연결 실패');
          reject(new Error('SSE failed'));
        };
      });
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      if (mountedRef.current && msg !== 'SSE failed') setError(msg);
    } finally {
      if (mountedRef.current) {
        setIsEmbedding(false);
        setEmbedProgress(0);
        setProgressMessage('');
      }
    }
  }, [showNotice]);

  // 리뷰 목록 조회 (shop_reviews 페이지네이션)
  // 인자 미지정 시 현재 state 값 사용 — 페이지/필터 변경은 setter 후 별도 호출.
  const fetchReviewList = useCallback(async (
    page?: number,
    pageSize?: number,
    ratingMin?: number | null,
    ratingMax?: number | null,
  ) => {
    const p = page ?? reviewListPage;
    const ps = pageSize ?? reviewListPageSize;
    const rmin = ratingMin === undefined ? reviewListRatingFilter.min : ratingMin;
    const rmax = ratingMax === undefined ? reviewListRatingFilter.max : ratingMax;
    setIsReviewListLoading(true);
    try {
      const params = new URLSearchParams({ page: String(p), page_size: String(ps) });
      if (rmin !== null) params.set('rating_min', String(rmin));
      if (rmax !== null) params.set('rating_max', String(rmax));
      const data = await apiFetch<ReviewListResponse>(`${API_BASE}/list?${params.toString()}`);
      if (mountedRef.current) {
        setReviewList(data.items);
        setReviewListTotal(data.total);
        setReviewListPage(data.page);
        setReviewListPageSize(data.page_size);
        setReviewListHasMore(data.has_more);
      }
    } catch {
      // 401/네트워크 에러 시 빈 리스트 — 사용자 흐름 막지 않음
      if (mountedRef.current) {
        setReviewList([]);
        setReviewListTotal(0);
        setReviewListHasMore(false);
      }
    } finally {
      if (mountedRef.current) setIsReviewListLoading(false);
    }
  }, [reviewListPage, reviewListPageSize, reviewListRatingFilter.min, reviewListRatingFilter.max]);

  // 설정 조회
  const fetchSettings = useCallback(async () => {
    try {
      const data = await apiFetch<AnalysisSettings>(`${API_BASE}/settings`);
      setSettings(data);
    } catch {
      // 설정 없으면 기본값 유지
    }
  }, []);

  // 설정 변경
  const updateSettings = useCallback(async (update: Partial<AnalysisSettings>) => {
    try {
      const data = await apiFetch<AnalysisSettings>(`${API_BASE}/settings`, {
        method: 'PUT',
        body: JSON.stringify(update),
      });
      setSettings(data);
      return data;
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg);
      return null;
    }
  }, []);

  // 초기 로드 — UX 정책: 페이지를 빈 상태로 초기화한다.
  //   "임베딩 저장" → /embed/stream
  //   "AI 분석 실행" → /analyze/stream → 완료 후 fetchAnalysis() 가 결과를 가져와 채운다.
  // 따라서 fetchAnalysis / fetchTrends 는 마운트 시 호출하지 않는다 (이전 분석 기록이 있어도 빈 상태로 시작).
  // 설정과 리뷰 목록은 영구 데이터이므로 마운트 시 한 번 로드한다.
  useEffect(() => {
    fetchSettings();
    fetchReviewList(1, 10, null, null);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 마운트 1회만; 의존성 추가 시 무한 재호출
  }, []);

  // 언마운트 시 활성 EventSource 정리 — TCP/SSE 커넥션 + setState-after-unmount 방지
  // React 18 StrictMode 대응:
  //   useRef(true) 는 한 번만 평가되므로, dev 의 mount→cleanup→re-mount 사이클에서
  //   cleanup 이 mountedRef=false 로 만들면 re-mount 후에도 false 로 박혀서
  //   "if (mountedRef.current) setIsLoading(false)" 가 영구히 스킵됨 → 무한 로딩.
  //   따라서 effect 본체에서 매 마운트마다 true 로 명시 복원해야 한다.
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      analyzeEsRef.current?.close();
      analyzeEsRef.current = null;
      embedEsRef.current?.close();
      embedEsRef.current = null;
      if (noticeTimerRef.current !== null) {
        window.clearTimeout(noticeTimerRef.current);
        noticeTimerRef.current = null;
      }
    };
  }, []);

  return {
    analysis,
    isLoading,
    isAnalyzing,
    isEmbedding,
    embedProgress,
    analyzeProgress,
    progressMessage,
    notice,
    error,
    analyzeReviews,
    fetchAnalysis,
    searchResults,
    isSearching,
    searchReviews,
    trends,
    anomalies,
    fetchTrends,
    downloadReport,
    embedReviews,
    settings,
    updateSettings,
    // 리뷰 목록 (shop_reviews 페이지네이션)
    reviewList,
    reviewListTotal,
    reviewListPage,
    reviewListPageSize,
    reviewListHasMore,
    isReviewListLoading,
    reviewListRatingFilter,
    setReviewListRatingFilter,
    fetchReviewList,
  };
}
