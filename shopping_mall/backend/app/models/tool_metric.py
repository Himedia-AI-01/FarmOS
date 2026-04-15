from datetime import datetime
from sqlalchemy import Boolean, Integer, String, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class ToolMetric(Base):
    __tablename__ = "shop_tool_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_log_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("shop_chat_logs.id"), nullable=True,
    )
    session_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tool_name: Mapped[str] = mapped_column(String(50), nullable=False)
    intent: Mapped[str] = mapped_column(String(50), nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    empty_result: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    iteration: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=func.now())
