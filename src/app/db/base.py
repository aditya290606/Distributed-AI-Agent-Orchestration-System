"""SQLAlchemy 2.0 DeclarativeBase and reusable model mixins."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, types
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base declarative class for all SQLAlchemy ORM models."""

    # Automatically generate __repr__ for clean debugging and logging
    def __repr__(self) -> str:
        attrs = ", ".join(f"{k}={v!r}" for k, v in self.__dict__.items() if not k.startswith("_"))
        return f"{self.__class__.__name__}({attrs})"


class UUIDPrimaryKeyMixin:
    """Mixin providing a UUID primary key compatible with MySQL.

    MySQL does not feature a native UUID type. SQLAlchemy's `types.Uuid(as_uuid=True)`
    stores standard RFC 4122 UUIDs as CHAR(36), providing portability, readability,
    and automatic Python uuid.UUID conversion.
    """

    id: Mapped[uuid.UUID] = mapped_column(
        types.Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )


class TimestampMixin:
    """Mixin providing created_at and updated_at UTC timestamps.

    MySQL DATETIME columns do not store timezone offsets natively.
    Therefore, all timestamps are normalized at the application layer to UTC
    using `datetime.now(UTC)` to guarantee chronological consistency.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )
