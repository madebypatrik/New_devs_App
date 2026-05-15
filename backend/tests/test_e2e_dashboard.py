"""
E2E tests for the revenue dashboard API.

These tests run against the live server at http://localhost:8000.
Start the environment first: docker-compose up --build

JWT tokens are minted locally using the same SECRET_KEY the backend uses
("debug_challenge_secret"). The TenantResolver maps emails to tenant IDs:
  sunset@propertyflow.com  → tenant-a (Sunset Properties)
  ocean@propertyflow.com   → tenant-b (Ocean Rentals)
"""

import pytest
import httpx
from decimal import Decimal
from datetime import datetime, timedelta, timezone
from jose import jwt

BASE_URL = "http://localhost:8000"
SECRET_KEY = "debug_challenge_secret"

# ── token helpers ────────────────────────────────────────────────────────────

def make_token(email: str) -> str:
    """Mint a valid HS256 JWT accepted by the backend's custom JWT path."""
    payload = {
        "sub": f"test-{email}",
        "id": f"test-{email}",
        "email": email,
        "aud": "authenticated",
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        "app_metadata": {},
        "user_metadata": {},
    }
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")

SUNSET_TOKEN = make_token("sunset@propertyflow.com")   # → tenant-a
OCEAN_TOKEN  = make_token("ocean@propertyflow.com")    # → tenant-b

def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── DoD #2 & #3: tenant isolation ────────────────────────────────────────────

class TestCacheTenantIsolation:
    """
    DoD: Log in as each client and view prop-001 — each sees their own data only.
    Both tenants have a property with id prop-001.
    Sunset (tenant-a) has 4 reservations totalling 2250.000.
    Ocean (tenant-b) has 0 reservations for prop-001 (their prop-001 has none seeded).
    """

    def test_sunset_gets_own_revenue_for_prop_001(self):
        resp = httpx.get(
            f"{BASE_URL}/api/v1/dashboard/summary",
            params={"property_id": "prop-001"},
            headers=auth(SUNSET_TOKEN),
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["property_id"] == "prop-001"
        # Sunset (tenant-a) has 4 reservations for prop-001
        assert data["reservations_count"] == 4, (
            f"Expected 4 reservations for tenant-a prop-001, got {data['reservations_count']}"
        )

    def test_ocean_gets_own_revenue_for_prop_001(self):
        resp = httpx.get(
            f"{BASE_URL}/api/v1/dashboard/summary",
            params={"property_id": "prop-001"},
            headers=auth(OCEAN_TOKEN),
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["property_id"] == "prop-001"
        # Ocean (tenant-b) has 0 reservations for prop-001 in seed data
        assert data["reservations_count"] == 0, (
            f"Expected 0 reservations for tenant-b prop-001, got {data['reservations_count']}"
        )

    def test_tenants_see_different_totals_for_same_property_id(self):
        """The core cache isolation test: same property_id, different tenant → different data."""
        sunset_resp = httpx.get(
            f"{BASE_URL}/api/v1/dashboard/summary",
            params={"property_id": "prop-001"},
            headers=auth(SUNSET_TOKEN),
        )
        ocean_resp = httpx.get(
            f"{BASE_URL}/api/v1/dashboard/summary",
            params={"property_id": "prop-001"},
            headers=auth(OCEAN_TOKEN),
        )
        assert sunset_resp.status_code == 200
        assert ocean_resp.status_code == 200

        sunset_total = sunset_resp.json()["total_revenue"]
        ocean_total  = ocean_resp.json()["total_revenue"]

        assert sunset_total != ocean_total, (
            "CACHE LEAK: Both tenants received the same total_revenue for prop-001. "
            "Cache key is not tenant-isolated."
        )

    def test_second_request_uses_cache_and_still_isolates(self):
        """A repeated request should hit Redis cache but still return the correct tenant's data."""
        for _ in range(2):
            resp = httpx.get(
                f"{BASE_URL}/api/v1/dashboard/summary",
                params={"property_id": "prop-001"},
                headers=auth(SUNSET_TOKEN),
            )
            assert resp.status_code == 200
            assert resp.json()["reservations_count"] == 4

        for _ in range(2):
            resp = httpx.get(
                f"{BASE_URL}/api/v1/dashboard/summary",
                params={"property_id": "prop-001"},
                headers=auth(OCEAN_TOKEN),
            )
            assert resp.status_code == 200
            assert resp.json()["reservations_count"] == 0


# ── DoD #5: total_revenue is a string (no float conversion) ──────────────────

class TestRevenuePrecision:
    """
    DoD: total_revenue must be a string in the API response (not a JSON number)
    to preserve NUMERIC(10,3) sub-cent precision.
    """

    def test_total_revenue_is_string_not_number(self):
        resp = httpx.get(
            f"{BASE_URL}/api/v1/dashboard/summary",
            params={"property_id": "prop-001"},
            headers=auth(SUNSET_TOKEN),
        )
        assert resp.status_code == 200
        raw_json = resp.text
        data = resp.json()

        # The JSON body must serialise total_revenue as a quoted string, not a bare number
        assert isinstance(data["total_revenue"], str), (
            f"total_revenue should be a string to preserve decimal precision, "
            f"got {type(data['total_revenue']).__name__}: {data['total_revenue']}"
        )

        # Confirm it does not appear as a bare JSON number in the raw response
        assert '"total_revenue": "' in raw_json or '"total_revenue":"' in raw_json, (
            "total_revenue is serialised as a JSON number, not a quoted string — "
            "float conversion is losing sub-cent precision"
        )

    def test_prop_001_tenant_a_total_is_exact(self):
        """
        DoD: prop-001 (tenant-a) total = 1250.000 + 333.333 + 333.333 + 333.334 = 2250.000
        This must match exactly — no float rounding drift.
        """
        resp = httpx.get(
            f"{BASE_URL}/api/v1/dashboard/summary",
            params={"property_id": "prop-001"},
            headers=auth(SUNSET_TOKEN),
        )
        assert resp.status_code == 200
        total_str = resp.json()["total_revenue"]

        # Parse with Decimal to avoid introducing float error in the test itself
        total = Decimal(total_str)
        expected = Decimal("1250.000") + Decimal("333.333") + Decimal("333.333") + Decimal("333.334")

        assert total == expected, (
            f"Revenue total has drifted: expected {expected}, got {total}. "
            "float() conversion is introducing IEEE 754 rounding error."
        )

    def test_sub_cent_amounts_do_not_drift(self):
        """
        333.333 × 3 + small amounts must not drift due to floating-point arithmetic.
        The DB stores NUMERIC(10,3); if the backend converts to float, 333.333 cannot
        be represented exactly and accumulated error appears in the sum.
        """
        resp = httpx.get(
            f"{BASE_URL}/api/v1/dashboard/summary",
            params={"property_id": "prop-001"},
            headers=auth(SUNSET_TOKEN),
        )
        assert resp.status_code == 200
        total = Decimal(resp.json()["total_revenue"])

        # Naive float arithmetic of the same values would give a different result
        float_total = 1250.000 + 333.333 + 333.333 + 333.334
        float_as_decimal = Decimal(str(round(float_total, 3)))

        # The API result should match exact Decimal arithmetic, not float arithmetic
        assert total == Decimal("2250.000"), (
            f"Expected exact Decimal 2250.000 but got {total}. "
            f"(Float arithmetic gives {float_as_decimal})"
        )


# ── DoD: response schema is correct ─────────────────────────────────────────

class TestResponseSchema:
    """Verify the API response has all required fields in the right shape."""

    def test_response_has_all_required_fields(self):
        resp = httpx.get(
            f"{BASE_URL}/api/v1/dashboard/summary",
            params={"property_id": "prop-001"},
            headers=auth(SUNSET_TOKEN),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "property_id" in data
        assert "total_revenue" in data
        assert "currency" in data
        assert "reservations_count" in data

    def test_unauthenticated_request_returns_401(self):
        resp = httpx.get(
            f"{BASE_URL}/api/v1/dashboard/summary",
            params={"property_id": "prop-001"},
        )
        assert resp.status_code == 401

    def test_different_properties_return_different_counts(self):
        """Smoke test: prop-002 (tenant-a) has 4 reservations."""
        resp = httpx.get(
            f"{BASE_URL}/api/v1/dashboard/summary",
            params={"property_id": "prop-002"},
            headers=auth(SUNSET_TOKEN),
        )
        assert resp.status_code == 200
        assert resp.json()["reservations_count"] == 4
