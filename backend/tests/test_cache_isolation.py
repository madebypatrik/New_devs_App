"""
Unit tests for Bug 1: cache key must include tenant_id.

These run without a real Redis or server — they test the cache key
logic directly from the source module.
"""

import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock


# ── DoD: cache key format ─────────────────────────────────────────────────────

class TestCacheKeyFormat:
    """The cache key must encode both property_id and tenant_id."""

    def test_cache_key_contains_property_id(self):
        from app.services.cache import get_revenue_summary
        import inspect
        source = inspect.getsource(get_revenue_summary)
        assert "property_id" in source

    def test_cache_key_contains_tenant_id(self):
        """Regression test for Bug 1: cache key was f'revenue:{property_id}' — missing tenant."""
        from app.services.cache import get_revenue_summary
        import inspect
        source = inspect.getsource(get_revenue_summary)
        # The key must reference tenant_id, not just property_id
        assert "tenant_id" in source, (
            "Cache key does not include tenant_id — cross-tenant data leakage possible"
        )

    def test_cache_key_format_is_tenant_scoped(self):
        """The literal cache_key line must include both IDs."""
        from app.services.cache import get_revenue_summary
        import inspect
        source = inspect.getsource(get_revenue_summary)
        # Verify the combined format is present
        assert "property_id}:{tenant_id}" in source or "tenant_id}:{property_id}" in source, (
            "Cache key does not combine property_id and tenant_id. "
            "Old broken pattern was f'revenue:{property_id}' — tenant_id was missing."
        )


# ── DoD: different tenants get different cache entries ────────────────────────

class TestCacheIsolation:
    """Two tenants with the same property_id must never share a Redis entry."""

    @pytest.mark.asyncio
    async def test_tenant_a_and_b_use_different_cache_keys(self):
        """
        Given: both tenant-a and tenant-b have prop-001
        When: each requests revenue
        Then: each writes to a distinct Redis key
        """
        stored_keys = []

        mock_redis = MagicMock()
        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.setex = AsyncMock(side_effect=lambda key, ttl, val: stored_keys.append(key))

        fake_revenue = {
            "property_id": "prop-001",
            "tenant_id": "tenant-a",
            "total": "2250.000",
            "currency": "USD",
            "count": 4,
        }

        with patch("app.services.cache.redis_client", mock_redis), \
             patch("app.services.reservations.calculate_total_revenue", AsyncMock(return_value=fake_revenue)):
            from app.services.cache import get_revenue_summary

            await get_revenue_summary("prop-001", "tenant-a")
            await get_revenue_summary("prop-001", "tenant-b")

        assert len(stored_keys) == 2, "Expected two separate cache writes, one per tenant"
        assert stored_keys[0] != stored_keys[1], (
            f"Cache keys are identical for tenant-a and tenant-b: {stored_keys[0]!r}. "
            "Data leakage: one tenant will read the other's cached revenue."
        )

    @pytest.mark.asyncio
    async def test_cached_value_is_not_served_to_different_tenant(self):
        """
        Given: tenant-a's revenue for prop-001 is cached
        When: tenant-b requests prop-001
        Then: tenant-b does NOT receive tenant-a's cached data
        """
        tenant_a_data = {
            "property_id": "prop-001",
            "tenant_id": "tenant-a",
            "total": "2250.000",
            "currency": "USD",
            "count": 4,
        }
        tenant_b_data = {
            "property_id": "prop-001",
            "tenant_id": "tenant-b",
            "total": "0.00",
            "currency": "USD",
            "count": 0,
        }

        # Simulate: tenant-a's key is cached, tenant-b's key is a miss
        def fake_redis_get(key: str):
            if "tenant-a" in key:
                return AsyncMock(return_value=json.dumps(tenant_a_data))()
            return AsyncMock(return_value=None)()

        mock_redis = MagicMock()
        mock_redis.get = fake_redis_get
        mock_redis.setex = AsyncMock()

        with patch("app.services.cache.redis_client", mock_redis), \
             patch("app.services.reservations.calculate_total_revenue", AsyncMock(return_value=tenant_b_data)):
            from app.services.cache import get_revenue_summary

            result_b = await get_revenue_summary("prop-001", "tenant-b")

        assert result_b["tenant_id"] == "tenant-b", (
            "tenant-b received tenant-a's cached data — cache key is not tenant-scoped"
        )
        assert result_b["total"] == "0.00", (
            f"tenant-b received wrong revenue total {result_b['total']!r} — likely got tenant-a's cached data"
        )

    @pytest.mark.asyncio
    async def test_cache_hit_returns_same_tenant_data(self):
        """A cache hit must return data for the requesting tenant, not another."""
        cached_data = {
            "property_id": "prop-001",
            "tenant_id": "tenant-a",
            "total": "2250.000",
            "currency": "USD",
            "count": 4,
        }

        mock_redis = MagicMock()
        mock_redis.get = AsyncMock(return_value=json.dumps(cached_data))
        mock_redis.setex = AsyncMock()

        with patch("app.services.cache.redis_client", mock_redis):
            from app.services.cache import get_revenue_summary
            result = await get_revenue_summary("prop-001", "tenant-a")

        assert result["total"] == "2250.000"
        assert result["tenant_id"] == "tenant-a"
