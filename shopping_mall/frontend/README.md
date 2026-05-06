# FarmOS Shopping Mall Frontend

React + Vite 기반 쇼핑몰 프론트엔드입니다. 기본 개발 포트는 `5174`입니다.

## URL Configuration

외부 서비스 URL은 `src/lib/serviceUrls.ts`에서 한 곳에 모아 관리합니다. 배포/테스트 환경에서는 `.env`에 `VITE_*` 값을 지정하고, 지정하지 않으면 로컬 개발용 `localhost` 주소로 fallback합니다.

```ts
VITE_API_URL ?? 'http://localhost:4000'
VITE_FARMOS_API_URL ?? 'http://localhost:8000/api/v1'
VITE_FARMOS_LOGIN_URL ?? 'http://localhost:5173/login'
```

선택 `.env` 예시:

```env
VITE_API_URL=http://localhost:4000
VITE_FARMOS_API_URL=http://localhost:8000/api/v1
VITE_FARMOS_LOGIN_URL=http://localhost:5173/login
```

## Run

```bash
npm install
npm run dev
```

## Verify

```bash
npm run test -- src/lib/serviceUrls.test.ts
npm run build
```
