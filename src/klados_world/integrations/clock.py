"""Standard-library implementation of the application wall-clock port."""

from datetime import UTC, datetime


class SystemClock:
    """Read UTC wall time without holding mutable global state."""

    def now(self) -> datetime:
        """Return a timezone-aware UTC instant."""
        return datetime.now(UTC)
