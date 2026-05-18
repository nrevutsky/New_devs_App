"""
Regression: monthly revenue boundaries must anchor to the property's local timezone,
not naive UTC. `res-tz-1` is stored at `2024-02-29 23:30:00+00`; in Europe/Paris that
is `2024-03-01 00:30`, so a Paris property's March bucket must include it.

We don't run real SQL — we capture the bound `start_utc` / `end_utc` from a fake
async session and assert the UTC window covers `res-tz-1` for Paris and excludes it
for UTC.
"""

from datetime import datetime, timezone
from decimal import Decimal

from app.services.reservations import calculate_monthly_revenue


class FakeRow:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class FakeResult:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class FakeSession:
    """Records SQL bind params. Returns a property timezone then a revenue total."""

    def __init__(self, property_tz: str | None, total: Decimal = Decimal("1250.000")):
        self.property_tz = property_tz
        self.total = total
        self.captured: list[dict] = []
        self._call = 0

    async def execute(self, statement, params):
        self.captured.append(params)
        self._call += 1
        if self._call == 1:
            # First query: SELECT timezone FROM properties …
            row = FakeRow(timezone=self.property_tz) if self.property_tz else None
            return FakeResult(row)
        # Second query: SELECT SUM(total_amount) …
        return FakeResult(FakeRow(total=self.total))


async def test_paris_property_march_window_includes_res_tz_1():
    session = FakeSession(property_tz="Europe/Paris")

    result = await calculate_monthly_revenue(
        property_id="prop-001",
        tenant_id="tenant-a",
        month=3,
        year=2024,
        db_session=session,
    )

    assert result == Decimal("1250.000")

    # The 2nd captured call has the UTC window we built.
    sum_params = session.captured[1]
    start_utc = sum_params["start_utc"]
    end_utc = sum_params["end_utc"]
    res_tz_1 = datetime(2024, 2, 29, 23, 30, tzinfo=timezone.utc)

    assert start_utc <= res_tz_1 < end_utc, (
        f"Paris-anchored March window must include res-tz-1 "
        f"({res_tz_1.isoformat()}); got [{start_utc.isoformat()}, {end_utc.isoformat()})"
    )
    # Paris in early March is UTC+1 (CET) → March starts at 2024-02-29 23:00 UTC.
    assert start_utc == datetime(2024, 2, 29, 23, 0, tzinfo=timezone.utc)


async def test_utc_property_march_window_excludes_res_tz_1():
    """Control: a UTC-anchored property correctly puts res-tz-1 in February."""
    session = FakeSession(property_tz="UTC", total=Decimal("0"))

    await calculate_monthly_revenue(
        property_id="prop-utc",
        tenant_id="tenant-a",
        month=3,
        year=2024,
        db_session=session,
    )

    sum_params = session.captured[1]
    res_tz_1 = datetime(2024, 2, 29, 23, 30, tzinfo=timezone.utc)
    assert sum_params["start_utc"] == datetime(2024, 3, 1, tzinfo=timezone.utc)
    assert not (sum_params["start_utc"] <= res_tz_1 < sum_params["end_utc"])


async def test_query_scoped_by_tenant_and_property():
    """Sanity: every SQL call must include both `property_id` and `tenant_id`."""
    session = FakeSession(property_tz="Europe/Paris")

    await calculate_monthly_revenue(
        property_id="prop-001",
        tenant_id="tenant-a",
        month=3,
        year=2024,
        db_session=session,
    )

    for params in session.captured:
        assert params.get("property_id") == "prop-001"
        assert params.get("tenant_id") == "tenant-a"
