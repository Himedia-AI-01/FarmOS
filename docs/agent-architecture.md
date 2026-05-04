# FarmOS Farm Agent — Architecture

End-to-end view of the Deep Agent stack: request preprocessing, orchestrator,
subagents (sync + async), tools, RAG pipeline, external services, persistence,
and observability.

## How to render

- **GitHub / GitLab**: this file renders the Mermaid diagram automatically.
- **VS Code**: install the *Markdown Preview Mermaid Support* extension, then
  open this file with `Ctrl+Shift+V`.
- **PNG / SVG export**: paste the Mermaid block into [mermaid.live](https://mermaid.live)
  → *Actions* → *Download PNG* (or SVG).
- **Standalone HTML**: open `docs/agent-architecture.html` directly in a browser.

## Diagram

```mermaid
graph TB
    User([User<br/>Browser / Mobile])

    subgraph Frontend["Frontend (React + TS)"]
        FAC[FarmAgentConsole.tsx]
        UFA[useFarmAgent.ts<br/>SSE stream consumer]
    end

    subgraph API["FastAPI /farm-agent endpoints"]
        ASK["POST /ask"]
        STREAM["POST /stream<br/>SSE tokens + events"]
        VOICE["POST /voice<br/>STT then ask"]
        IMG["POST /diagnose-image<br/>multimodal"]
        BRIEF["GET /briefing<br/>daily summary"]
        APPROVE["POST /approve-action<br/>HITL IoT control"]
    end

    subgraph Preprocess["Request Preprocessing"]
        FP[fast_path.try_fast_path<br/>regex → direct tool call<br/>~0.3-2s, no LLM]
        DR[_wrap_with_routing_hint<br/>single-domain detection<br/>injects ROUTING_HINT]
        SUP[skip-orchestrator-synthesis<br/>SSE flag — emit subagent verbatim]
        DEDUP[message-id dedup<br/>+ reasoning-block filter]
    end

    subgraph Agent["Deep Agent v0.5 Orchestrator"]
        ORCH{Grok 4.1 Fast<br/>via OpenRouter<br/>parallel_tool_calls=true}
        HP[HarnessProfile<br/>Grok-tuned suffix<br/>SummarizationMW excluded]
        MM[MemoryMiddleware<br/>AGENTS.md auto-load]
        CKPT[(Postgres Checkpointer<br/>thread_id = userId:sessionId)]
    end

    subgraph SyncSubs["Subagents — sync via task tool"]
        DA[diagnosis-agent<br/>병해충 진단·방제]
        SA[subsidy-agent<br/>공익직불 RAG·자격]
        FDA[farm-data-agent<br/>날씨·시세·일지·IoT]
    end

    subgraph AsyncSubs["Async Subagents — ASGI in-process"]
        VA[verifier-agent<br/>graph_id from langgraph.json<br/>background safety check]
    end

    subgraph DiagTools["Diagnosis Tools"]
        DP[diagnose_pest<br/>+ deterministic safety gate<br/>희석배수·살포시기 검증]
    end

    subgraph SubsidyTools["Subsidy Tools — Agentic RAG"]
        SRF["search_subsidy_regulations_fast<br/>~500ms · auto-escalate if sim&lt;0.5"]
        SRP[search_subsidy_regulations<br/>full hybrid + reranker]
        SOC[search_subsidy_obligation_check<br/>hypothesis-driven yes/no<br/>parallel 2-query fanout]
        ES[list_eligible_subsidies]
        CER[check_eligibility_rule]
        GSD[get_subsidy_details]
    end

    subgraph RAG["Subsidy RAG Pipeline"]
        SE[Solar embedding<br/>Upstage asymmetric]
        BM[Kiwi morphological tokenize<br/>+ BM25]
        RRF[Reciprocal Rank Fusion<br/>k=60]
        RR[bge-reranker-v2-m3-ko<br/>cross-encoder]
        CRX[(ChromaDB<br/>gov_subsidy collection)]
        CCX[Contextual Prefix Cache<br/>JSON disk cache]
    end

    subgraph FarmTools["Farm Data Tools"]
        PROF[get_my_farm_profile]
        GW[get_current_weather]
        GP[get_market_prices]
        GMC[get_market_prices_for_crop<br/>재배→유통 alias]
        GJ[journal queries]
        GI[IoT decisions]
    end

    subgraph External["External APIs"]
        KMA[기상청 KMA<br/>단기·중기예보]
        KAM[KAMIS<br/>농산물 시세]
        NCPMS[NCPMS<br/>국가 병해충]
        RDA[RDA 농약안전정보]
        FOOD[식품안전나라]
        RP[RunPod<br/>pest classifier]
        ORT[OpenRouter<br/>Grok 4.1 Fast]
        UP[Upstage<br/>Solar embedding<br/>+ Document Parse]
    end

    subgraph Persist["Persistence"]
        PG[(Postgres<br/>FarmOS DB<br/>users · journals · IoT)]
        AGM[memory/AGENTS.md<br/>domain priors]
    end

    subgraph Obs["Observability + Eval"]
        LS[LangSmith Tracing<br/>per-run trees · cost · latency]
        EVAL[scripts/eval_farm_agent.py<br/>6 examples · 5 evaluators<br/>must_contain · citation_present<br/>verdict_correct · latency_ok]
    end

    %% Flow edges
    User --> Frontend
    Frontend --> API

    ASK --> FP
    STREAM --> FP
    VOICE --> FP

    FP -->|hit| GW
    FP -->|hit| GP
    FP -->|miss| DR
    DR --> ORCH

    ORCH -.->|profile| HP
    ORCH -.->|context| MM
    ORCH -.->|state| CKPT

    ORCH -->|task| DA
    ORCH -->|task| SA
    ORCH -->|task| FDA
    DA -.->|background| VA

    DA --> DP
    DA --> PROF

    SA --> SRF
    SA --> SOC
    SA --> ES
    SA --> CER
    SA --> GSD
    SA --> PROF
    SRF -.->|auto-escalate| SRP
    SOC -.->|parallel x2| SRF

    FDA --> GW
    FDA --> GP
    FDA --> GMC
    FDA --> GJ
    FDA --> GI
    FDA --> PROF

    SRP --> SE
    SRP --> BM
    SE --> RRF
    BM --> RRF
    RRF --> RR
    RR --> CRX
    SRP -.-> CCX

    DP --> NCPMS
    DP --> RDA
    DP --> FOOD

    GW --> KMA
    GP --> KAM
    GMC --> KAM

    IMG --> RP

    ORCH --> ORT
    SE --> UP

    PROF --> PG
    GJ --> PG
    GI --> PG
    MM --> AGM

    STREAM -.->|emit| SUP
    STREAM -.->|filter| DEDUP

    ORCH -.->|trace| LS
    EVAL -.->|score| LS
    EVAL --> ORCH

    classDef external fill:#fef3c7,stroke:#f59e0b,color:#000
    classDef subagent fill:#dbeafe,stroke:#3b82f6,color:#000
    classDef asyncsub fill:#fae8ff,stroke:#a855f7,color:#000
    classDef tool fill:#dcfce7,stroke:#16a34a,color:#000
    classDef storage fill:#fce7f3,stroke:#db2777,color:#000
    classDef obs fill:#e0e7ff,stroke:#6366f1,color:#000
    classDef preprocess fill:#fee2e2,stroke:#ef4444,color:#000

    class KMA,KAM,NCPMS,RDA,FOOD,RP,ORT,UP external
    class DA,SA,FDA subagent
    class VA asyncsub
    class DP,SRF,SRP,SOC,ES,CER,GSD,GW,GP,GMC,GJ,GI,PROF tool
    class CRX,CCX,CKPT,PG,AGM storage
    class LS,EVAL obs
    class FP,DR,SUP,DEDUP preprocess
```

## Color legend

| Color | Meaning |
|---|---|
| 🟧 amber | External API (KMA, KAMIS, OpenRouter, Upstage, NCPMS, RDA, RunPod) |
| 🟦 blue | Synchronous subagent (delegated via Deep Agents `task` tool) |
| 🟪 purple | Async subagent (background ASGI, deepagents v0.5+) |
| 🟩 green | Tool — read-only data lookup or RAG retrieval |
| 🟧 pink | Persistence (Postgres, ChromaDB, file-based memory) |
| 🟦 indigo | Observability (LangSmith tracing, eval harness) |
| 🟥 red | Request-time preprocessing / streaming filter |

## Key flows

### A. Simple weather/price query (fast path, no LLM)
`User → /stream → fast_path.try_fast_path → tool → response (~0.3-2s)`

### B. Subsidy obligation question ("영농일지 꼭 써야 해?")
`User → /stream → fast_path miss → routing_hint(subsidy-agent) → orchestrator (1 tool_call) → task(subsidy-agent) → search_subsidy_obligation_check → parallel SRF×2 → verdict_hint → answer (~3-6s)`

### C. Pest diagnosis from image
`/diagnose-image → RunPod classifier → orchestrator → task(diagnosis-agent) → diagnose_pest (NCPMS+RDA+KMA) → safety gate → answer; verifier-agent runs async in background`

### D. Multi-topic decomposition ("자격 + 처벌")
`Orchestrator emits 2 tool_calls in one round → LangGraph parallel edges → 2× search_subsidy_regulations_fast concurrently → orchestrator synthesizes (~4-8s vs serial ~8-14s)`

## Optimization layers (recent)

1. **Fast-path** — regex shortcut for the top ~5 query patterns (weather, price, journal, IoT history) bypasses the LLM entirely.
2. **Direct routing hint** — single-domain queries (clear "직불금" / "병해충" keyword) skip the orchestrator's routing-decision LLM call.
3. **Parallel tool calls** — `parallel_tool_calls=True` lets the LLM emit multiple `task` / `search` calls in one round; LangGraph runs them on parallel edges.
4. **Auto-escalation** — `search_subsidy_regulations_fast` automatically retries with the full reranker if `top_sim < 0.5`. Deterministic, not prompt-dependent.
5. **Skip orchestrator synthesis** — after `task` returns, SSE emits the subagent's verbatim answer; the orchestrator's redundant paraphrasing is suppressed.
6. **Reasoning OFF default** — Grok reasoning ON multiplied per-call latency by 5×; off by default, opt-in via env.
7. **Hypothesis-driven yes/no** — `search_subsidy_obligation_check` fans out two queries (mandatory + optional hypotheses) and compares similarity to derive a verdict_hint deterministically.
8. **Async verifier** — verifier-agent runs in background ASGI; user sees diagnosis immediately while safety cross-check completes.
9. **Message dedup + reasoning-block filter** — eliminates duplicate response bubbles caused by Grok's reasoning content blocks.
10. **HarnessProfile (Grok)** — Grok-tuned system suffix and `SummarizationMiddleware` exclusion to prevent citation truncation on long contexts.

## ReasoningBank failure-mining pipeline

Two parallel queues, one shared toolchain. Both feed proposals into separate
human-review files; **nothing is auto-merged into agent runtime memory**.

### Strategy queue (offline, eval-driven)

```
eval_farm_agent.py            STRATEGY_CANDIDATES.md     STRATEGY_PROPOSALS.md
   │ (failed scores)             │ (## ⏳ ts — tags)         │ (## R-prop. ...)
   ▼                              ▼                              ▼
 _append_strategy_candidate ─► distill_strategies.py ──► reviewer copies
                                  │                          to STRATEGIES.md
                                  └─ --analyze: histogram
```

### Diagnosis queue (runtime, verifier-driven)

```
verifier-agent (FAIL/UNKNOWN)  DIAGNOSIS_CANDIDATES.md   DIAGNOSIS_PROPOSALS.md
   │ (PASS/FAIL/UNKNOWN)          │ (## ⏳ ts — tags)         │ (## D-prop. ...)
   ▼                              ▼                              ▼
 record_verifier_verdict ────► distill_strategies.py    reviewer files code
   (SSE delegation hook)         --diagnosis             change for tools/
                                  │                          routing/prompt
                                  └─ --analyze-diagnosis:
                                     verdict + question
                                     histogram
```

### Operator commands

```bash
cd backend

# Snapshot the strategy queue (post-eval failures)
.venv/Scripts/python scripts/distill_strategies.py --analyze

# Snapshot the diagnosis queue (verifier disagreements)
.venv/Scripts/python scripts/distill_strategies.py --analyze-diagnosis

# Generate strategy proposals (LLM call) — review before promoting to STRATEGIES.md
.venv/Scripts/python scripts/distill_strategies.py

# Generate diagnosis proposals (LLM call) — file as code-change reviews
.venv/Scripts/python scripts/distill_strategies.py --diagnosis

# Offline stub mode (no LLM, useful in CI / smoke tests)
.venv/Scripts/python scripts/distill_strategies.py --dry-run
.venv/Scripts/python scripts/distill_strategies.py --diagnosis --dry-run
```

### Schema distinctions

| Queue | Stub heading | Fields | Reviewer action |
|---|---|---|---|
| Strategy (`R-prop.`) | `## R-prop.` | `When` / `Strategy` / `Pitfall` | Edit `STRATEGIES.md`, loaded into every LLM call |
| Diagnosis (`D-prop.`) | `## D-prop.` | `When` / `Fix` / `Pitfall` | File code change to a tool / routing / verifier prompt |

### Pinned contracts (CI-enforced)

The regression suite (`scripts/test_distill_loop.py`, 48 cases) pins:

- **Verifier output format** — `VERIFIER_PROMPT` must mandate exactly `PASS|FAIL|UNKNOWN` as the leading verdict token; the `verifier_candidates.parse_verdict` regex must accept exactly those three.
- **Fast-path safety** — `BLOCKLIST` regex must continue to block 농약 / 진단 / 직불 / 보조금 / 시행지침 query patterns.
- **Verdict calibration** — `SUBSIDY_PROMPT` must contain the `UNCLEAR` rule + 담당 권장 phrasing + ⚠ marker.
- **Briefing sanitizer** — meta-reasoning preamble removal before any Korean morning brief renders.
- **Eval JSON summary schema** — `--summary-json` v1 fields stable for CI gating via `jq`.

CI gate: [`.github/workflows/farm-agent-tests.yml`](../.github/workflows/farm-agent-tests.yml). Live eval (gated, manual-dispatch only): [`.github/workflows/farm-agent-eval.yml`](../.github/workflows/farm-agent-eval.yml).

## File map

| Concern | Files |
|---|---|
| API endpoints | [backend/app/api/farm_agent.py](../backend/app/api/farm_agent.py) |
| Agent build + LLM + profiles | [backend/app/services/farm_agent/agent.py](../backend/app/services/farm_agent/agent.py) |
| Tools | [backend/app/services/farm_agent/tools.py](../backend/app/services/farm_agent/tools.py) |
| Prompts | [backend/app/services/farm_agent/prompts.py](../backend/app/services/farm_agent/prompts.py) |
| Fast path | [backend/app/services/farm_agent/fast_path.py](../backend/app/services/farm_agent/fast_path.py) |
| Subsidy RAG | [backend/app/services/subsidy/gov_rag.py](../backend/app/services/subsidy/gov_rag.py) |
| Async verifier graph | [backend/app/services/farm_agent/verifier_graph.py](../backend/app/services/farm_agent/verifier_graph.py) |
| Verifier candidate writer (iter 19) | [backend/app/services/farm_agent/verifier_candidates.py](../backend/app/services/farm_agent/verifier_candidates.py) |
| LangGraph manifest | [backend/langgraph.json](../backend/langgraph.json) |
| Eval harness | [backend/scripts/eval_farm_agent.py](../backend/scripts/eval_farm_agent.py) |
| Distill + analyze (both queues) | [backend/scripts/distill_strategies.py](../backend/scripts/distill_strategies.py) |
| Regression suite (48 cases) | [backend/scripts/test_distill_loop.py](../backend/scripts/test_distill_loop.py) |
| ReasoningBank ledger | [docs/ralph-agent-upgrade.md](ralph-agent-upgrade.md) |
| Frontend hook | [frontend/src/hooks/useFarmAgent.ts](../frontend/src/hooks/useFarmAgent.ts) |
| Frontend UI | [frontend/src/components/agent/FarmAgentConsole.tsx](../frontend/src/components/agent/FarmAgentConsole.tsx) |
