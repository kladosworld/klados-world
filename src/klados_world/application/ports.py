"""Infrastructure contracts owned by the application layer."""

from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    """Supply timezone-aware UTC wall time, never society ticks.

    Society time must later advance through explicit, persisted domain operations.
    Implementations must not derive or advance society age from elapsed wall time.
    """

    def now(self) -> datetime:
        """Return the current wall-clock instant in UTC."""
        ...
