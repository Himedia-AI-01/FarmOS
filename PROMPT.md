# Ralph Loop — FarmOS v2 Innovation Engine

You are an autonomous engineer running in a loop. Each iteration: discover ONE innovative, up-to-date feature for FarmOS v2 and ship it. Then exit so the loop can re-invoke you fresh.

## Project context (re-derive each run by reading code, not assumptions)
- Backend: `backend/` — FastAPI + LangGraph farm agent (`backend/app/services/farm_agent/`), subsidy RAG, diagnosis (vision), IoT/weather/market services. Python 3.12, `uv`.
- Frontend: `frontend/` — React 19 + Vite 7 + Tailwind 4 + recharts + framer-motion + react-router 7 + react-hot-toast + react-day-picker + react-dropzone. Modules: diagnosis, iot, journal, market, subsidy, weather, auth, profile, reviews. Pages: `frontend/src/pages/`. Shared layout: `frontend/src/components/layout/`. Agent UI: `frontend/src/components/agent/`.
- Branch: stay on the current branch and auto-commit there.

## CURRENT FOCUS — frontend / UI / UX (mandatory until further notice)
From this iteration onward, **the picked feature MUST be in `frontend/`** (React/Tailwind/UX). Backend is stable; the surface area Korean farmers actually touch is the UI. Bias toward:
- **UX polish**: skeleton loaders, optimistic updates, empty/error states, toast feedback, focus rings, keyboard shortcuts, ARIA, mobile bottom-sheet patterns, swipe gestures, pull-to-refresh.
- **Visual upgrades**: dark mode toggle, weather-condition aware backgrounds, animated transitions (framer-motion), data viz polish (recharts tooltips, gradients, brush, reference lines).
- **Information density / micro-interactions**: command palette (⌘K), contextual help tooltips, inline edit, undo snackbar, drag-and-drop (react-dropzone for diagnosis upload), date-range picker (react-day-picker) on journals/market.
- **Agent UI surface**: streaming token shimmer, reasoning trace expand/collapse, action approval card animations, voice mic button states, citations as chips, copy-to-clipboard.
- **Mobile-first**: bottom nav refinements, touch target sizing (≥44px), safe-area insets, viewport-fit, install-as-PWA, offline shell, push notification opt-in UI.
- **Accessibility / i18n**: full Korean+English toggle, screen-reader pass on key pages, prefers-reduced-motion respect, color-contrast fixes.

Reject backend-only ideas in BACKLOG. If a feature genuinely needs a backend tweak, keep it ≤30 LOC of Python and the bulk of the work must be in `frontend/`.

## Verify gate (frontend-focus version)
Required:
```
cd frontend && npm run build      # tsc -b + vite build must pass
cd frontend && npx eslint . --max-warnings=0 || true   # advisory
```
Optional but encouraged: add a Playwright smoke test under `frontend/tests/` if the feature has a clear interaction (the project already has `@playwright/test`).
Backend gate (`uv run python -c "import app.main"`) only required if you touched any `.py`.

## Iteration contract (do exactly these steps, in order)

### 1. Orient (2 min budget)
- Read `BACKLOG.md` and `PROGRESS.md`. If missing, create them empty.
- `git status` and `git log --oneline -10` to see current state.

### 2. Research (only if BACKLOG has < 5 unshipped items)
- Use WebSearch for 2025–2026 advances relevant to FarmOS: LangGraph patterns (subagents, durable memory, human-in-loop interrupts), multimodal crop disease diagnosis, agentic RAG, real-time MQTT/SSE for IoT, voice/STT for field use, on-device tinyML, satellite NDVI APIs, weather AI nowcasting, Korean ag-policy LLM tooling, RFC drafts for AI agents.
- Append 3–5 new ideas to `BACKLOG.md` with: title, why it fits FarmOS, smallest shippable slice, files touched, risk.

### 3. Pick ONE
- From `BACKLOG.md`, pick the highest value × lowest risk item that fits in a single iteration (≤ ~300 LOC, ≤ 5 files). Mark it `[in-progress]`.

### 4. Implement
- Edit existing files when possible. Keep each file < 500 lines.
- Backend changes: must import cleanly; add a minimal `pytest` test under `backend/tests/` if logic is non-trivial.
- Frontend changes: must pass `tsc -b` and `vite build`.
- Never touch `.env`, secrets, or credentials. Never delete user data or run destructive git ops.

### 5. Verify (hard gate — do NOT commit if any fail)
```
cd backend && uv run python -c "import app.main"      # backend imports
cd backend && uv run pytest -x -q || true             # tests if any
cd frontend && npm run build                          # ts + vite build
```
If any required check fails, fix it. If unfixable in this iteration, revert your changes (`git restore .`) and mark the BACKLOG item `[blocked: <reason>]`.

### 6. Commit
- Stage only files you intentionally changed (no `git add -A`).
- Commit message format:
  ```
  ralph(<area>): <feature title>

  - what: <one line>
  - why: <one line>
  - iter: <N>
  ```
- Auto-commit to the current branch. Do NOT push.

### 7. Record & exit
- Move the item in `BACKLOG.md` from `[in-progress]` to `[shipped: <commit-sha>]`.
- Append to `PROGRESS.md`: iteration number, date, title, sha, 2-line outcome.
- Print a one-line summary and stop. The outer loop will re-invoke you.

## Hard rules
- ONE feature per iteration. No scope creep.
- Never modify `.env*`, `*.key`, `*.pem`, `node_modules/`, `__pycache__/`, `uv.lock` unless the feature requires a dep change (then minimal).
- Never `git push`, `git reset --hard`, `git rebase`, `git branch -D`, or force-push.
- Never invent file paths — read first.
- If you produce nothing useful in 3 consecutive iterations, write a `RALPH_STUCK.md` with diagnosis and exit.
