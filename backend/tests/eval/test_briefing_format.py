"""Briefing format lint — deterministic structural checks.

No LLM call. Reads cached briefings from `farm_agent_briefings` (or generates
one if cache empty for the seeded test user) and runs each check against it.
Catches markdown-table regressions, divider abuse, missing greeting, meta
reasoning leaks.
"""

from __future__ import annotations

import re
import pytest

from tests.eval.conftest import load_jsonl


pytestmark = pytest.mark.eval


async def _load_recent_briefing() -> str | None:
    """Pull the most recent briefing from the cache table for any user."""
    try:
        from sqlalchemy import select

        from app.core.database import async_session
        from app.services.farm_agent.briefing import FarmAgentBriefing

        async with async_session() as db:
            row = await db.execute(
                select(FarmAgentBriefing)
                .order_by(FarmAgentBriefing.generated_at.desc())
                .limit(1)
            )
            r = row.scalar_one_or_none()
            return r.content if r else None
    except Exception:
        return None


_EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF✀-➿]"
)


_BRIEFING_CONTENT_CACHE: dict[str, str] = {}


async def _ensure_briefing() -> str | None:
    """Get a briefing from cache, generating one if none exists.

    Earlier the test skipped when no briefing was cached, which made the eval
    silently incomplete. Now we generate a fresh briefing for the seeded test
    user (farmer01) so the format checks always run.
    """
    if "content" in _BRIEFING_CONTENT_CACHE:
        return _BRIEFING_CONTENT_CACHE["content"]

    content = await _load_recent_briefing()
    if content:
        _BRIEFING_CONTENT_CACHE["content"] = content
        return content

    # No cached briefing — generate one for the seeded test user.
    try:
        from app.services.farm_agent.briefing import get_or_generate_briefing
        from psycopg.rows import dict_row
        from psycopg_pool import AsyncConnectionPool
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from sqlalchemy.engine.url import make_url

        from app.core.config import settings
        from app.services.farm_agent import build_farm_agent

        url = make_url(settings.DATABASE_URL).set(drivername="postgresql")
        pg_dsn = url.render_as_string(hide_password=False)
        pool = AsyncConnectionPool(
            conninfo=pg_dsn,
            max_size=2,
            kwargs={"autocommit": True, "row_factory": dict_row},
            open=False,
        )
        await pool.open()
        try:
            checkpointer = AsyncPostgresSaver(pool)
            await checkpointer.setup()
            agent = build_farm_agent(checkpointer=checkpointer, mcp_tools=[])
            generated, _ = await get_or_generate_briefing(
                agent=agent,
                user_id="farmer01",
                user_name="김사과",
                force_regenerate=True,
            )
        finally:
            await pool.close()

        _BRIEFING_CONTENT_CACHE["content"] = generated
        return generated
    except Exception as exc:
        pytest.skip(f"Could not generate briefing: {exc}")
        return None


@pytest.mark.parametrize("check", load_jsonl("briefing_format"))
async def test_briefing_format(check, record_result):
    content = await _ensure_briefing()
    if not content:
        pytest.skip("Briefing unavailable.")

    name = check["check"]
    passed = True
    detail = ""

    if "forbidden" in check:
        for phrase in check["forbidden"]:
            if phrase in content:
                passed = False
                detail = f"forbidden phrase {phrase!r} present"
                break
        else:
            detail = f"all forbidden phrases absent ({len(check['forbidden'])} checked)"

    elif name == "no_excessive_emoji":
        emoji_count = len(_EMOJI_RE.findall(content))
        passed = emoji_count <= check["max_emoji"]
        detail = f"emoji_count={emoji_count} max={check['max_emoji']}"

    elif name == "min_length":
        passed = len(content) >= check["min_chars"]
        detail = f"length={len(content)} min={check['min_chars']}"

    elif name == "max_length":
        passed = len(content) <= check["max_chars"]
        detail = f"length={len(content)} max={check['max_chars']}"

    elif name == "address_term":
        hits = sum(content.count(term) for term in check["required"])
        passed = hits >= check["min_count"]
        detail = f"address_terms={hits} min={check['min_count']}"

    record_result(
        surface="briefing_format",
        case_id=name,
        passed=passed,
        detail=detail,
        query=f"check={name}",
        answer=content[:2000] if not passed else "",
    )
    assert passed, f"{name}: {detail}\n--- briefing head ---\n{content[:500]}"
