# Railway Deployment — FarmOS-v2

Two services in one Railway project: `backend` (FastAPI + uv) and `frontend` (Vite/React, served as static via `serve`). Plus Postgres and Redis plugins.

## 1. Create the project

1. New Project → Empty Project.
2. Add plugins: **PostgreSQL** and **Redis**.

## 2. Add the backend service

1. New Service → GitHub Repo → select this repo, branch `deploy`.
2. Settings → **Root Directory**: `backend`
3. Settings → Builder: **Nixpacks** (auto from `backend/nixpacks.toml`).
4. Networking → **Generate Domain**.
5. Volumes → **Add Volume** mounted at `/data` (persists `chroma_data` + uploads).
6. Variables → paste the `SERVICE: backend` block from [.env.railway.example](.env.railway.example).
   - `DATABASE_URL` and `REDIS_URL` use Railway references (`${{Postgres.DATABASE_URL}}`).
   - Generate a strong `JWT_SECRET_KEY` (e.g. `openssl rand -hex 32`).
7. Deploy. Healthcheck hits `/api/v1/health`.

## 3. Add the frontend service

1. New Service → GitHub Repo → same repo + branch.
2. Settings → **Root Directory**: `frontend`
3. Settings → Builder: **Nixpacks** (auto from `frontend/nixpacks.toml`).
4. Networking → **Generate Domain**.
5. Variables — set the three `VITE_*` vars from [.env.railway.example](.env.railway.example), pointing at the backend's public domain. Vite bakes these at build time, so changing them requires a redeploy.
6. Deploy. SPA fallback is handled by `serve -s`.

## 4. Post-deploy

- Update backend `CORS_ORIGINS` to include the frontend's Railway URL, then redeploy backend.
- Bootstrap subsidy index (one-time, ~5–15 min, calls Upstage):
  ```
  railway run --service backend "cd /app && uv run subsidy-ingest"
  ```
  Output lands on the mounted volume at `/data/chroma`, so it survives restarts.
- If you switch `SUBSIDY_RAG_BACKEND=redis`, run the Redis index build per `backend/app/services/subsidy/` docs.

## Notes

- Vercel config has been removed (`frontend/vercel.json`). SPA rewrites are handled by `serve -s`.
- Backend `main.py` (uvicorn `reload=True` on :8000) is dev-only; Railway uses the start command from `backend/nixpacks.toml` directly.
- The volume only mounts on the backend service. Don't put state in the frontend.
- LangCache, LangSmith, MCP servers, IoT relay, Pest classifier — all auto-disable when their keys are blank, so you can leave them empty until needed.
