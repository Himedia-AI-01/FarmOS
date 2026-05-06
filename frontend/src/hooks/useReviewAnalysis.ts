// Design Ref: §6.3 — useReviewAnalysis Hook
import { useState, useEffect, useCallback, useRef } from 'react';
import type { AnalysisResult, SearchResult, TrendData, AnomalyAlert, AnalysisSettings } from '@/types';

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
  const [settings, setSettings] = useState<AnalysisSettings>({
    auto_batch_enabled: false,
    batch_trigger_count: 10,
    batch_schedule: null,
    default_batch_size: 50,
  });

  // 활성 EventSource 추적 — 컴포넌트 언마운트 시 close 보장 (메모리 누수 + setState-after-unmount 방지)
  const analyzeEsRef = useRef<EventSource | null>(null);
  const embedEsRef = useRef<EventSource | null>(null);
  const mountedRef = useRef(true);

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
          if (data.progress >= 100) {
            es.close();
            analyzeEsRef.current = null;
            if (mountedRef.current) fetchAnalysis();
            resolve();
          }
          if (data.error) {
            es.close();
            analyzeEsRef.current = null;
            if (mountedRef.current) setError(data.error);
            reject(new Error(data.error));
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
  }, [fetchAnalysis]);

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
  }, []);

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

  // 초기 로드
  useEffect(() => {
    fetchAnalysis();
    fetchTrends();
    fetchSettings();
  }, [fetchAnalysis, fetchTrends, fetchSettings]);

  // 언마운트 시 활성 EventSource 정리 — TCP/SSE 커넥션 + setState-after-unmount 방지
  useEffect(() => {
    return () => {
      mountedRef.current = false;
      analyzeEsRef.current?.close();
      analyzeEsRef.current = null;
      embedEsRef.current?.close();
      embedEsRef.current = null;
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
  };
}
