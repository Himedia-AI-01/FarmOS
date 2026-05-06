"""Weather API routes."""

from fastapi import APIRouter, Depends

from app.core.deps import get_current_user
from app.core.weather_client import get_weather
from app.models.user import User
from app.services.farm_agent.weather_alerts import build_task_advisories

router = APIRouter(prefix="/weather", tags=["weather"])


@router.get("/current")
async def get_current_weather(_user: User = Depends(get_current_user)) -> dict:
    """Return current KMA weather and timestamped ultra-short forecasts."""
    return await get_weather()


@router.get("/task-advisories")
async def get_task_advisories(user: User = Depends(get_current_user)) -> dict:
    """Per-day agentic task decisions for the next 5 days.

    Same policy engine the farm-agent uses internally (`analyze_weather_risks`)
    so the UI's 작업 판단 cards match the briefing/answer the agent would give
    if asked. Crop-aware: uses the logged-in user's `main_crop` to attach
    작물별 힌트 (사과·토마토·벼 등) without the frontend knowing the policy.
    """
    weather = await get_weather()
    items = build_task_advisories(weather, main_crop=user.main_crop or None)
    return {"items": items, "main_crop": user.main_crop or None}
