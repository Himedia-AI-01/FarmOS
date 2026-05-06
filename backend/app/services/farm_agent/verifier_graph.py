"""Standalone verifier graph for AsyncSubAgent (Deep Agents v0.5+).

Why a separate file:
  AsyncSubAgent resolves `graph_id` against the manifest in `langgraph.json`,
  which expects an importable `graph` symbol. Co-locating verifier inline in
  `agent.py` would create a circular import (agent.py imports the verifier,
  the verifier graph imports the same model builder). This file is the single
  importable entry point for the verifier-agent's compiled graph.

Behavior:
  Identical contract to the previous inline `verifier-agent` SubAgent dict —
  the orchestrator delegates with (pest, crop, region, verified_answer), the
  verifier re-runs `diagnose_pest` and emits PASS/FAIL/UNKNOWN.

When unused:
  If deepagents<0.5 (no AsyncSubAgent class), `agent.py` falls back to the
  synchronous SubAgent dict and never imports this file. So an old deepagents
  install doesn't pay any cost.
"""

from __future__ import annotations

import logging

# LangGraph v1.0 deprecated `langgraph.prebuilt.create_react_agent` (removed
# in v2.0). The replacement lives in `langchain.agents.create_agent` with
# `prompt=` renamed to `system_prompt=`. We try the new import first and fall
# back to the deprecated name so the project continues to work on older
# pinned langchain installs.
try:
    from langchain.agents import create_agent as _create_agent
    _USE_LEGACY_KW = False
except ImportError:  # pragma: no cover — old langchain
    from langgraph.prebuilt import create_react_agent as _create_agent
    _USE_LEGACY_KW = True

from app.services.farm_agent.prompts import VERIFIER_PROMPT
from app.services.farm_agent.tools import DIAGNOSIS_TOOLS

logger = logging.getLogger(__name__)


def _build_verifier_model():
    """Build the LLM for the verifier graph.

    Lazy import of `_build_llm` to avoid circular imports — `agent.py` imports
    this module's `graph` symbol, so we cannot import from `agent.py` at module
    top-level.
    """
    from app.services.farm_agent.agent import _build_llm
    return _build_llm()


# `graph` is the symbol AsyncSubAgent looks up via langgraph.json's
# graphs["verifier-agent"] = "./...:graph" pointer.
#
# Lazy construction: building the LLM at module import requires
# OPENROUTER_API_KEY / LITELLM_URL to be set. CI tooling and the langgraph CLI
# may import this module just to enumerate the manifest, where env may be
# absent. `get_graph()` defers construction; `graph` is exposed as a
# module-level proxy that calls `get_graph()` on first attribute access so
# existing `verifier_graph:graph` references in langgraph.json keep working.

_compiled_graph = None


def get_graph():
    """Build (and cache) the verifier graph. Safe to call repeatedly."""
    global _compiled_graph
    if _compiled_graph is not None:
        return _compiled_graph
    _prompt_kwarg = (
        {"prompt": VERIFIER_PROMPT} if _USE_LEGACY_KW else {"system_prompt": VERIFIER_PROMPT}
    )
    _compiled_graph = _create_agent(
        model=_build_verifier_model(),
        tools=DIAGNOSIS_TOOLS,
        name="verifier-agent",
        **_prompt_kwarg,
    )
    return _compiled_graph


class _LazyGraph:
    """Defers graph compilation until first attribute/call access."""

    def __getattr__(self, name):
        return getattr(get_graph(), name)

    def __call__(self, *args, **kwargs):
        return get_graph()(*args, **kwargs)


graph = _LazyGraph()
