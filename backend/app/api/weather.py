"""Weather API routes."""

from fastapi import APIRouter, Depends

from app.core.deps import get_current_user
from app.core.weather_client import get_weather
from app.models.user import User

router = APIRouter(prefix="/weather", tags=["weather"])


@router.get("/current")
async def get_current_weather(_user: User = Depends(get_current_user)) -> dict:
    """Return current KMA weather and timestamped ultra-short forecasts."""
    return await get_weather()
