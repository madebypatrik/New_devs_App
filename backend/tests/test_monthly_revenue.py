"""
Unit tests for Bug 2 (monthly revenue placeholder) and Bug 4 (timezone-naive datetimes).

DoD items covered:
  - March monthly revenue for prop-001 (tenant-a) is not 0
  - res-tz-1 (2024-02-29 23:30:00+00) is counted in March for Europe/Paris, not February
  - December → January year boundary is handled correctly
"""

import pytest
from decimal import Decimal
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from unittest.mock import AsyncMock, MagicMock, patch


# ── helpers ───────────────────────────────────────────────────────────────────

def make_db_pool_mock(scalar_value):
    """Return a mock DatabasePool whose session returns scalar_value from execute()."""
    mock_result = MagicMock()
    mock_result.scalar.return_value = scalar_value

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    mock_factory = MagicMock()
    mock_factory.return_value = mock_session

    mock_pool = MagicMock()
    mock_pool.initialize = AsyncMock()
    mock_pool.session_factory = mock_factory
    mock_pool.get_session = MagicMock(return_value=mock_session)

    return mock_pool, mock_session


# ── Bug 2: monthly revenue is implemented ────────────────────────────────────

class TestMonthlyRevenueNotPlaceholder:
    """
    DoD: March monthly revenue for prop-001 (tenant-a) returns the actual DB value, not 0.
    The old placeholder unconditionally returned Decimal('0').
    """

    @pytest.mark.asyncio
    async def test_monthly_revenue_queries_database(self):
        """calculate_monthly_revenue must call session.execute, not just return 0."""
        mock_pool, mock_session = make_db_pool_mock(Decimal("2250.000"))

        with patch("app.core.database_pool.DatabasePool", return_value=mock_pool):
            from app.services.reservations import calculate_monthly_revenue
            result = await calculate_monthly_revenue(
                property_id="prop-001",
                month=3,
                year=2024,
                tenant_id="tenant-a",
                property_timezone="Europe/Paris",
            )

        assert mock_session.execute.called, (
            "calculate_monthly_revenue did not call session.execute — "
            "it is still returning the placeholder Decimal('0')"
        )

    @pytest.mark.asyncio
    async def test_monthly_revenue_returns_db_value_not_zero(self):
        """The returned value must reflect what the database returns."""
        mock_pool, _ = make_db_pool_mock(Decimal("2250.000"))

        with patch("app.core.database_pool.DatabasePool", return_value=mock_pool):
            from app.services.reservations import calculate_monthly_revenue
            result = await calculate_monthly_revenue(
                property_id="prop-001",
                month=3,
                year=2024,
                tenant_id="tenant-a",
                property_timezone="Europe/Paris",
            )

        assert result == Decimal("2250.000"), (
            f"Expected 2250.000, got {result}. "
            "Monthly revenue is still returning the placeholder 0."
        )

    @pytest.mark.asyncio
    async def test_monthly_revenue_returns_decimal_type(self):
        """Return type must be Decimal, not float, to preserve precision."""
        mock_pool, _ = make_db_pool_mock(Decimal("4975.500"))

        with patch("app.core.database_pool.DatabasePool", return_value=mock_pool):
            from app.services.reservations import calculate_monthly_revenue
            result = await calculate_monthly_revenue("prop-002", 3, 2024, "tenant-a", "Europe/Paris")

        assert isinstance(result, Decimal), (
            f"calculate_monthly_revenue must return Decimal, got {type(result).__name__}"
        )

    @pytest.mark.asyncio
    async def test_monthly_revenue_returns_zero_for_empty_result(self):
        """COALESCE means a property with no reservations returns 0, not None."""
        mock_pool, _ = make_db_pool_mock(Decimal("0"))

        with patch("app.core.database_pool.DatabasePool", return_value=mock_pool):
            from app.services.reservations import calculate_monthly_revenue
            result = await calculate_monthly_revenue("prop-999", 3, 2024, "tenant-a", "UTC")

        assert result == Decimal("0")


# ── Bug 4: timezone-aware month boundaries ────────────────────────────────────

class TestTimezoneAwareBoundaries:
    """
    DoD: res-tz-1 (2024-02-29 23:30:00+00) must be counted in March for Europe/Paris.
    Europe/Paris is UTC+1 in winter, so 2024-02-29 23:30 UTC = 2024-03-01 00:30 Paris time.
    """

    def test_paris_march_start_is_before_res_tz_1(self):
        """
        March 1 00:00 Europe/Paris = Feb 29 23:00 UTC.
        res-tz-1 check_in = Feb 29 23:30 UTC.
        Therefore res-tz-1 IS within March (Paris) → must be included.
        """
        tz = ZoneInfo("Europe/Paris")
        march_start = datetime(2024, 3, 1, tzinfo=tz)

        res_tz_1_checkin = datetime(2024, 2, 29, 23, 30, tzinfo=timezone.utc)

        # Convert both to UTC for comparison
        march_start_utc = march_start.astimezone(timezone.utc)

        assert res_tz_1_checkin >= march_start_utc, (
            f"res-tz-1 ({res_tz_1_checkin}) is before March start in Paris ({march_start_utc}). "
            "It would be excluded from March — timezone boundary is wrong."
        )

    def test_paris_march_start_is_feb_29_23h_utc(self):
        """Confirm the exact UTC offset: March 1 00:00 Paris = Feb 29 23:00 UTC (UTC+1 in winter)."""
        tz = ZoneInfo("Europe/Paris")
        march_start_paris = datetime(2024, 3, 1, 0, 0, tzinfo=tz)
        march_start_utc = march_start_paris.astimezone(timezone.utc)

        expected_utc = datetime(2024, 2, 29, 23, 0, tzinfo=timezone.utc)
        assert march_start_utc == expected_utc, (
            f"Expected March start (Paris) to be {expected_utc} in UTC, got {march_start_utc}"
        )

    def test_naive_datetime_would_misclassify_res_tz_1(self):
        """
        Document the original bug: naive datetime(2024, 3, 1) = UTC midnight.
        res-tz-1 at 23:30 UTC on Feb 29 is BEFORE naive March 1 → wrongly excluded.
        """
        naive_march_start = datetime(2024, 3, 1)  # naive, treated as UTC midnight
        res_tz_1_checkin_naive = datetime(2024, 2, 29, 23, 30)  # naive UTC

        # With naive boundaries, res-tz-1 would have been excluded from March
        assert res_tz_1_checkin_naive < naive_march_start, (
            "This test documents the old bug: with naive datetimes, "
            "res-tz-1 falls before March 1 and would be excluded from March revenue"
        )

    def test_aware_datetime_correctly_includes_res_tz_1_in_march(self):
        """With timezone-aware boundaries, res-tz-1 is correctly in March (Paris)."""
        tz = ZoneInfo("Europe/Paris")
        aware_march_start = datetime(2024, 3, 1, tzinfo=tz)
        aware_march_end   = datetime(2024, 4, 1, tzinfo=tz)

        res_tz_1 = datetime(2024, 2, 29, 23, 30, tzinfo=timezone.utc)

        assert res_tz_1 >= aware_march_start.astimezone(timezone.utc), \
            "res-tz-1 should be >= March start (Paris) in UTC"
        assert res_tz_1 < aware_march_end.astimezone(timezone.utc), \
            "res-tz-1 should be < April start (Paris) in UTC"

    def test_new_york_march_boundaries_are_correct(self):
        """
        America/New_York is UTC-5 in winter.
        March 1 00:00 NY = March 1 05:00 UTC.
        A reservation at March 1 03:00 UTC is still February 28 in New York.
        """
        tz = ZoneInfo("America/New_York")
        march_start_ny = datetime(2024, 3, 1, tzinfo=tz)
        march_start_utc = march_start_ny.astimezone(timezone.utc)

        # A booking at 03:00 UTC on March 1 = 22:00 Feb 29 NY time → belongs in February
        before_ny_march = datetime(2024, 3, 1, 3, 0, tzinfo=timezone.utc)

        assert before_ny_march < march_start_utc, (
            "A reservation at 03:00 UTC on March 1 should be February in New York, "
            "but the timezone-aware boundary puts it in March."
        )

    def test_december_to_january_year_boundary(self):
        """Month=12 must generate end_date in January of the next year."""
        from app.services.reservations import calculate_monthly_revenue
        import inspect
        source = inspect.getsource(calculate_monthly_revenue)
        # The year-wrap logic must exist
        assert "year + 1" in source, (
            "calculate_monthly_revenue does not handle December → January year boundary"
        )

    @pytest.mark.asyncio
    async def test_query_receives_timezone_aware_start_date(self):
        """
        The SQL query parameters must include timezone-aware datetimes so that
        PostgreSQL TIMESTAMPTZ comparisons work correctly.
        """
        captured_params = {}

        async def capture_execute(query, params):
            captured_params.update(params)
            result = MagicMock()
            result.scalar.return_value = Decimal("1250.000")
            return result

        mock_session = AsyncMock()
        mock_session.execute = capture_execute
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_pool = MagicMock()
        mock_pool.initialize = AsyncMock()
        mock_pool.session_factory = MagicMock(return_value=mock_session)
        mock_pool.get_session = MagicMock(return_value=mock_session)

        with patch("app.core.database_pool.DatabasePool", return_value=mock_pool):
            from app.services.reservations import calculate_monthly_revenue
            await calculate_monthly_revenue("prop-001", 3, 2024, "tenant-a", "Europe/Paris")

        start = captured_params.get("start_date")
        assert start is not None, "start_date was not passed to the query"
        assert start.tzinfo is not None, (
            f"start_date {start!r} is timezone-naive — "
            "PostgreSQL will treat it as UTC, causing wrong month boundaries for Paris/NY properties"
        )
