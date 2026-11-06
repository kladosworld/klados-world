"""Verify the clock contract independently of any society implementation."""

from datetime import UTC, datetime

from klados_world.application.ports import Clock
from klados_world.integrations.clock import SystemClock


def read_time(clock: Clock) -> datetime:
    return clock.now()


def test_system_clock_returns_current_aware_utc_time() -> None:
    before = datetime.now(UTC)
    actual = read_time(SystemClock())
    after = datetime.now(UTC)
    assert actual.tzinfo is UTC
    assert before <= actual <= after


def test_clock_port_accepts_an_injected_time_source() -> None:
    instant = datetime(2026, 1, 1, tzinfo=UTC)

    class FixedClock:
        def now(self) -> datetime:
            return instant

    assert read_time(FixedClock()) == instant
