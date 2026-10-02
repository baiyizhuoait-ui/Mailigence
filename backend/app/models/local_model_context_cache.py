"""LocalModelContextCache — remembered context-window probe results.

Keyed by (base_url, model): probing sends several real (potentially large)
test requests to the local inference server and can take tens of seconds, so
the outcome is persisted and reused until the user explicitly re-probes (the
probe endpoint upserts this row). Runtime resolution reads this table when a
profile has no manual num_ctx.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class LocalModelContextCache(Base):
    __tablename__ = "local_model_context_cache"
    __table_args__ = (
        UniqueConstraint("base_url", "model", name="uq_localmodel_ctx"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    base_url: Mapped[str] = mapped_column(String(255))
    model: Mapped[str] = mapped_column(String(120))
    # The context window the probe found safe for this environment.
    probed_num_ctx: Mapped[int] = mapped_column(BigInteger)
    # Model's full supported window (from /api/show), None when undetectable.
    model_max_context: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    probed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<LocalModelContextCache {self.base_url} {self.model!r} "
            f"probed={self.probed_num_ctx} max={self.model_max_context}>"
        )
