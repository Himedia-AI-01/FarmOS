/**
 * 백엔드 API 베이스 URL — 환경변수 우선, fallback 은 배포 도메인.
 *
 * 로컬 개발은 `VITE_API_BASE=http://localhost:8000/api/v1` 처럼 명시해서 사용한다.
 */
export const API_BASE: string =
  ((import.meta.env as Record<string, string | undefined>).VITE_API_BASE) ??
  "https://app.farmos.biz/api/v1";
