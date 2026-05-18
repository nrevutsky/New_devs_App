"""
Regression: two tenants must not share a Redis cache entry for the same property_id.

`properties.id` is not globally unique — `prop-001` exists for both `tenant-a` (Beach
House Alpha, Paris) and `tenant-b` (Mountain Lodge Beta, NYC). A cache key keyed only
by `property_id` cross-pollinates revenue between tenants. This test fails fast if
that key is reverted.
"""

import json
import pytest

from app.services import cache as cache_module


class FakeRedis:
    def __init__(self):
        self.store: dict[str, bytes] = {}

    async def get(self, key):
        return self.store.get(key)

    async def setex(self, key, _ttl, value):
        self.store[key] = value.encode() if isinstance(value, str) else value


async def test_same_property_id_does_not_leak_between_tenants(monkeypatch):
    fake_redis = FakeRedis()
    monkeypatch.setattr(cache_module, "redis_client", fake_redis)

    calls: list[tuple[str, str]] = []

    async def fake_calculate(property_id, tenant_id):
        calls.append((property_id, tenant_id))
        totals = {"tenant-a": "2250.000", "tenant-b": "0.00"}
        counts = {"tenant-a": 4, "tenant-b": 0}
        return {
            "property_id": property_id,
            "tenant_id": tenant_id,
            "total": totals[tenant_id],
            "currency": "USD",
            "count": counts[tenant_id],
        }

    monkeypatch.setattr(
        "app.services.reservations.calculate_total_revenue", fake_calculate
    )

    # tenant-a warms the cache for prop-001.
    a = await cache_module.get_revenue_summary("prop-001", "tenant-a")
    # tenant-b asks for the *same* property_id — must NOT see tenant-a's value.
    b = await cache_module.get_revenue_summary("prop-001", "tenant-b")

    assert a["total"] == "2250.000"
    assert b["total"] == "0.00", (
        "Cross-tenant cache leak: tenant-b received tenant-a's cached revenue"
    )
    assert ("prop-001", "tenant-a") in calls
    assert ("prop-001", "tenant-b") in calls

    # Per-tenant entries in the store.
    keys = list(fake_redis.store.keys())
    assert any("tenant-a" in k and "prop-001" in k for k in keys), keys
    assert any("tenant-b" in k and "prop-001" in k for k in keys), keys


async def test_second_call_same_tenant_hits_cache(monkeypatch):
    """Sanity check: caching still works within a single tenant (no regression)."""
    fake_redis = FakeRedis()
    monkeypatch.setattr(cache_module, "redis_client", fake_redis)

    call_count = 0

    async def fake_calculate(property_id, tenant_id):
        nonlocal call_count
        call_count += 1
        return {
            "property_id": property_id,
            "tenant_id": tenant_id,
            "total": "100.00",
            "currency": "USD",
            "count": 1,
        }

    monkeypatch.setattr(
        "app.services.reservations.calculate_total_revenue", fake_calculate
    )

    await cache_module.get_revenue_summary("prop-001", "tenant-a")
    await cache_module.get_revenue_summary("prop-001", "tenant-a")

    assert call_count == 1, "Second call should be served from cache"
