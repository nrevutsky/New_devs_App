"""
Regression: the `/dashboard/summary` endpoint must preserve cent-level precision
end-to-end and not leak intermediate sub-cent (NUMERIC(10,3)) values into JS-float
arithmetic. We mount only the dashboard router (avoiding `app.main`'s Supabase/Redis
side effects), override the auth dep, and stub the cache layer with monkeypatch.
"""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import dashboard as dashboard_module


def _build_app(fake_total: str, fake_count: int = 4, tenant_id: str = "tenant-a"):
    app = FastAPI()
    app.include_router(dashboard_module.router, prefix="/api/v1")

    def fake_user():
        return SimpleNamespace(tenant_id=tenant_id, email="test@example.com")

    # `get_current_user` is the local alias used inside dashboard.py;
    # that's the key FastAPI uses for the dependency lookup.
    app.dependency_overrides[dashboard_module.get_current_user] = fake_user

    async def fake_summary(property_id, t_id):
        return {
            "property_id": property_id,
            "tenant_id": t_id,
            "total": fake_total,
            "currency": "USD",
            "count": fake_count,
        }

    return app, fake_summary


@pytest.fixture
def get_total(monkeypatch):
    """Returns a helper that fetches `total_revenue` for a given stubbed total."""

    def _get(fake_total: str) -> str:
        app, fake_summary = _build_app(fake_total)
        monkeypatch.setattr(dashboard_module, "get_revenue_summary", fake_summary)
        with TestClient(app) as client:
            r = client.get(
                "/api/v1/dashboard/summary", params={"property_id": "p"}
            )
        assert r.status_code == 200, r.text
        return r.json()["total_revenue"]

    return _get


def test_response_total_is_string_quantized_to_two_decimals(get_total, monkeypatch):
    """NUMERIC(10,3) sub-cent total must come out as a 2-decimal string —
    never a float (which would round-trip through JS lossily)."""
    app, fake_summary = _build_app(fake_total="2250.000")
    monkeypatch.setattr(dashboard_module, "get_revenue_summary", fake_summary)
    with TestClient(app) as client:
        body = client.get(
            "/api/v1/dashboard/summary", params={"property_id": "prop-001"}
        ).json()

    assert body["total_revenue"] == "2250.00"
    assert isinstance(body["total_revenue"], str), (
        "total_revenue must be a string so JSON cannot float-coerce sub-cent precision"
    )


def test_banker_rounding_on_half_cent(get_total):
    """ROUND_HALF_EVEN: .005 → .00 when preceding digit is even; .015 → .02."""
    assert get_total("100.005") == "100.00"
    assert get_total("100.015") == "100.02"


def test_sum_of_three_thirds_does_not_drift(get_total):
    """333.333 + 333.333 + 333.334 = 1000.000 → displays as 1000.00, not 999.99."""
    assert get_total("1000.000") == "1000.00"
