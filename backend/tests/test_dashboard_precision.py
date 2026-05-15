"""
Unit tests for Bug 3: total_revenue must be returned as a string, not a float.

DoD items covered:
  - prop-001 (tenant-a) total = 2250.000 — no float drift
  - Displayed totals match DB values exactly
"""

import pytest
import pathlib
from decimal import Decimal
from unittest.mock import AsyncMock, patch, MagicMock


# ── source file path ──────────────────────────────────────────────────────────
BACKEND_ROOT = pathlib.Path(__file__).parent.parent
DASHBOARD_SRC = (BACKEND_ROOT / "app" / "api" / "v1" / "dashboard.py").read_text()


# ── Bug 3: str() vs float() — source-level checks ────────────────────────────

class TestRevenuePrecisionInDashboard:
    """The dashboard endpoint must not convert Decimal to float before returning."""

    def test_dashboard_source_does_not_use_float_conversion(self):
        """Regression guard: float(revenue_data['total']) must not exist in dashboard.py."""
        assert "float(" not in DASHBOARD_SRC, (
            "dashboard.py still uses float() conversion — "
            "this discards sub-cent precision from NUMERIC(10,3) DB values"
        )

    def test_dashboard_source_uses_str_for_total_revenue(self):
        """The total_revenue field must be wrapped in str() to preserve precision."""
        assert (
            "str(revenue_data['total'])" in DASHBOARD_SRC
            or 'str(revenue_data["total"])' in DASHBOARD_SRC
        ), "total_revenue is not serialised as str() — float conversion may be present"

    @pytest.mark.asyncio
    async def test_dashboard_returns_string_total_revenue(self):
        """The response dict from the endpoint must have total_revenue as a str."""
        fake_revenue = {
            "property_id": "prop-001",
            "tenant_id": "tenant-a",
            "total": "2250.000",
            "currency": "USD",
            "count": 4,
        }
        mock_user = MagicMock()
        mock_user.tenant_id = "tenant-a"

        with patch("app.services.cache.get_revenue_summary", AsyncMock(return_value=fake_revenue)):
            # Import here so the patch is already active
            from app.api.v1 import dashboard as dashboard_module
            result = await dashboard_module.get_dashboard_summary(
                property_id="prop-001", current_user=mock_user
            )

        assert isinstance(result["total_revenue"], str), (
            f"total_revenue is {type(result['total_revenue']).__name__}, expected str. "
            "Float conversion will cause sub-cent rounding errors."
        )

    @pytest.mark.asyncio
    async def test_sub_cent_amounts_preserved_exactly(self):
        """
        333.333 + 333.333 + 333.334 = 1000.000 exactly in Decimal.
        If float() is used the result drifts.
        """
        db_total = str(Decimal("333.333") + Decimal("333.333") + Decimal("333.334"))
        assert db_total == "1000.000"

        fake_revenue = {
            "property_id": "prop-test",
            "tenant_id": "tenant-a",
            "total": db_total,
            "currency": "USD",
            "count": 3,
        }
        mock_user = MagicMock()
        mock_user.tenant_id = "tenant-a"

        with patch("app.services.cache.get_revenue_summary", AsyncMock(return_value=fake_revenue)):
            from app.api.v1 import dashboard as dashboard_module
            result = await dashboard_module.get_dashboard_summary(
                property_id="prop-test", current_user=mock_user
            )

        assert result["total_revenue"] == "1000.000", (
            f"Expected '1000.000', got {result['total_revenue']!r}. "
            "float() was used: float(333.333)*3 gives a rounding error."
        )

    def test_float_would_lose_precision_for_sub_cent(self):
        """
        Document that float() IS lossy for NUMERIC(10,3) values.
        This test proves why str() is required.
        """
        a, b, c = Decimal("333.333"), Decimal("333.333"), Decimal("333.334")
        decimal_sum = a + b + c
        assert decimal_sum == Decimal("1000.000")

        # float() applied to the Decimal string loses trailing precision
        float_from_decimal = float(decimal_sum)
        as_str = str(decimal_sum)

        assert as_str == "1000.000", f"str(Decimal) preserves precision: {as_str}"
        # float loses the trailing zero
        assert str(float_from_decimal) in ("1000.0", "1000"), (
            f"float loses trailing precision zeros: {float_from_decimal}"
        )


# ── Decimal arithmetic integrity ──────────────────────────────────────────────

class TestDecimalArithmetic:
    """Verify that Decimal operations on the seed data produce exact expected values."""

    def test_prop_001_tenant_a_exact_sum(self):
        amounts = [
            Decimal("1250.000"),
            Decimal("333.333"),
            Decimal("333.333"),
            Decimal("333.334"),
        ]
        assert sum(amounts) == Decimal("2250.000")

    def test_prop_002_tenant_a_exact_sum(self):
        amounts = [
            Decimal("1250.00"),
            Decimal("1475.50"),
            Decimal("1199.25"),
            Decimal("1050.75"),
        ]
        assert sum(amounts) == Decimal("4975.50")

    def test_prop_003_tenant_a_exact_sum(self):
        assert sum([Decimal("2850.00"), Decimal("3250.50")]) == Decimal("6100.50")

    def test_prop_004_tenant_b_exact_sum(self):
        amounts = [
            Decimal("420.00"),
            Decimal("560.75"),
            Decimal("480.25"),
            Decimal("315.50"),
        ]
        assert sum(amounts) == Decimal("1776.50")

    def test_prop_005_tenant_b_exact_sum(self):
        assert sum([Decimal("920.00"), Decimal("1080.40"), Decimal("1255.60")]) == Decimal("3256.00")
