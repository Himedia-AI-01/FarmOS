import { useState, useEffect, useCallback, useRef } from 'react';
import type { SensorReading, IrrigationEvent, SensorAlert, ControlEvent } from '@/types';

const API_BASE = 'https://iot.lilpa.moe/api/v1';
const FULL_SYNC_INTERVAL = 60000; // 전체 동기화는 60초마다 (fallback)

interface SensorData {
  latest: SensorReading | null;
  history: SensorReading[];
  alerts: SensorAlert[];
  irrigations: IrrigationEvent[];
  connected: boolean;
}

// Design Ref: §4.4 — SSE control 이벤트 콜백 지원
type ControlEventHandler = (event: ControlEvent) => void;
const _controlHandlers: Set<ControlEventHandler> = new Set();

export function onControlEvent(handler: ControlEventHandler) {
  _controlHandlers.add(handler);
  return () => { _controlHandlers.delete(handler); };
}

// SSE ai_decision 이벤트 콜백
type AnyEventHandler = (data: unknown) => void;
const _aiDecisionHandlers: Set<AnyEventHandler> = new Set();

export function onAIDecisionEvent(handler: AnyEventHandler) {
  _aiDecisionHandlers.add(handler);
  return () => { _aiDecisionHandlers.delete(handler); };
}

// ── 모듈 싱글톤 EventSource (ref-counted) ─────────────────────────────────
// 과거: useSensorData()를 호출하는 컴포넌트마다 새 EventSource를 만들어
// /iot 와 / 가 동시에 마운트된 상태에서 두 SSE 커넥션이 동시에 열려 IoT 서버에 부하.
// 현재: 모듈 레벨 싱글톤 + 구독자 카운트로 관리. 마지막 구독자가 떠나면 close.
type SensorDataSetter = React.Dispatch<React.SetStateAction<SensorData>>;
type FailRef = { current: number };

let _sharedEs: EventSource | null = null;
let _sharedSetters: Set<SensorDataSetter> = new Set();
let _sharedFailRefs: Set<FailRef> = new Set();

function _broadcast(updater: (prev: SensorData) => SensorData): void {
  _sharedSetters.forEach((setter) => setter(updater));
}

function _bumpFail(): void {
  _sharedFailRefs.forEach((ref) => { ref.current += 1; });
}

function _resetFail(): void {
  _sharedFailRefs.forEach((ref) => { ref.current = 0; });
}

function _ensureSharedEs(): EventSource {
  if (_sharedEs && _sharedEs.readyState !== EventSource.CLOSED) return _sharedEs;
  const es = new EventSource(`${API_BASE}/sensors/stream`);
  _sharedEs = es;

  es.addEventListener('sensor', (e) => {
    try {
      const reading = JSON.parse(e.data) as SensorReading;
      _broadcast((prev) => ({
        ...prev,
        latest: reading,
        history: [...prev.history.slice(-99), reading],
        connected: true,
      }));
    } catch (err) { console.warn('[SSE] sensor parse error:', err); }
  });
  es.addEventListener('alert', (e) => {
    try {
      const alert = JSON.parse(e.data) as SensorAlert;
      _broadcast((prev) => ({ ...prev, alerts: [alert, ...prev.alerts] }));
    } catch (err) { console.warn('[SSE] alert parse error:', err); }
  });
  es.addEventListener('irrigation', (e) => {
    try {
      const event = JSON.parse(e.data) as IrrigationEvent;
      _broadcast((prev) => ({ ...prev, irrigations: [event, ...prev.irrigations] }));
    } catch (err) { console.warn('[SSE] irrigation parse error:', err); }
  });
  es.addEventListener('control', (e) => {
    try {
      const controlEvent = JSON.parse(e.data) as ControlEvent;
      _controlHandlers.forEach((h) => h(controlEvent));
    } catch (err) { console.warn('[SSE] control parse error:', err); }
  });
  es.addEventListener('ai_decision', (e) => {
    try {
      const decision = JSON.parse(e.data);
      _aiDecisionHandlers.forEach((h) => h(decision));
    } catch (err) { console.warn('[SSE] ai_decision parse error:', err); }
  });
  es.onopen = () => {
    _resetFail();
    _broadcast((prev) => ({ ...prev, connected: true }));
  };
  es.onerror = () => {
    _bumpFail();
    // 5회 연속 실패 시 connected=false (자동 재연결은 EventSource가 처리)
    let anyExceeded = false;
    _sharedFailRefs.forEach((ref) => { if (ref.current >= 5) anyExceeded = true; });
    if (anyExceeded) _broadcast((prev) => ({ ...prev, connected: false }));
  };
  return es;
}

function _releaseSharedEs(setter: SensorDataSetter, failRef: FailRef): void {
  _sharedSetters.delete(setter);
  _sharedFailRefs.delete(failRef);
  if (_sharedSetters.size === 0 && _sharedEs) {
    _sharedEs.close();
    _sharedEs = null;
  }
}

export function useSensorData() {
  const [data, setData] = useState<SensorData>({
    latest: null,
    history: [],
    alerts: [],
    irrigations: [],
    connected: false,
  });

  const failCount = useRef(0);

  // 전체 데이터 동기화 (초기 로드 + 주기적 fallback)
  const fetchAll = useCallback(async () => {
    try {
      const [latestRes, historyRes, alertsRes, irrigationsRes] = await Promise.all([
        fetch(`${API_BASE}/sensors/latest`),
        fetch(`${API_BASE}/sensors/history?limit=100`),
        fetch(`${API_BASE}/sensors/alerts`),
        fetch(`${API_BASE}/irrigation/events`),
      ]);

      const latest = await latestRes.json();
      const history = await historyRes.json();
      const alerts = await alertsRes.json();
      const irrigations = await irrigationsRes.json();

      failCount.current = 0;

      setData({
        latest: latest.timestamp ? latest : null,
        history,
        alerts,
        irrigations,
        connected: true,
      });
    } catch {
      failCount.current += 1;
      if (failCount.current >= 5) {
        setData(prev => ({ ...prev, connected: false }));
      }
    }
  }, []);

  // SSE 연결 — 싱글톤 EventSource를 ref-counting 으로 공유
  useEffect(() => {
    // 초기 전체 로드
    fetchAll();

    // 모듈 싱글톤 SSE 구독 등록 (이미 열려있으면 재사용)
    _sharedSetters.add(setData);
    _sharedFailRefs.add(failCount);
    _ensureSharedEs();

    // 전체 동기화 fallback (history 정합성 보장)
    const syncTimer = setInterval(fetchAll, FULL_SYNC_INTERVAL);

    return () => {
      _releaseSharedEs(setData, failCount);
      clearInterval(syncTimer);
    };
  }, [fetchAll]);

  return data;
}
