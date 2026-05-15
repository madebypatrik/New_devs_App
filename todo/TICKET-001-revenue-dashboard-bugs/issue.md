# TICKET-001: Fix Revenue Dashboard Bugs (3 Client-Reported Issues)

**Priority:** Critical  
**Type:** Bug Fix  
**Reported by:** Client A (Sunset Properties), Client B (Ocean Rentals), Finance Team  

---

## Problem Summary

Three separate issues were reported after the revenue dashboard went live:

1. **Cross-tenant data leakage** — Client B sometimes sees Client A's revenue numbers on refresh
2. **Revenue totals mismatch** — Client A's monthly figures don't match their internal records
3. **Off by a few cents** — Finance team notices small discrepancies in revenue totals

---

## Root Cause Analysis

Five bugs were identified across the backend and frontend. All are in existing code.

---

### BUG 1 — Critical: Cache key does not include tenant_id (cross-tenant leakage)

**File:** `backend/app/services/cache.py`, line 13  
**Symptom:** Client B (Ocean Rentals) sometimes sees Client A's revenue data on page refresh

Both tenants have a property with id `prop-001` (confirmed in `database/seed.sql`). The cache key is built using only `property_id`, so both tenants share the same Redis cache entry.

```python
# BROKEN
cache_key = f"revenue:{property_id}"

# FIX
cache_key = f"revenue:{property_id}:{tenant_id}"
```

---

### BUG 2 — Critical: Monthly revenue calculation always returns 0 (unimplemented placeholder)

**File:** `backend/app/services/reservations.py`, lines 9–32  
**Symptom:** Monthly revenue totals are always zero; Client A's board meeting numbers will be wrong

`calculate_monthly_revenue` has a SQL query drafted but never executes it — it returns `Decimal('0')` unconditionally.

```python
# BROKEN — placeholder, never hits the database
return Decimal('0') # Placeholder for now until DB connection is finalized

# FIX — implement using the same DB pool pattern used in calculate_total_revenue below
async with db_pool.get_session() as session:
    query = text("""
        SELECT COALESCE(SUM(total_amount), 0) as total
        FROM reservations
        WHERE property_id = :property_id
          AND tenant_id = :tenant_id
          AND check_in_date >= :start_date
          AND check_in_date < :end_date
    """)
    result = await session.execute(query, {
        "property_id": property_id,
        "tenant_id": tenant_id,
        "start_date": start_date,
        "end_date": end_date
    })
    return Decimal(str(result.scalar() or 0))
```

---

### BUG 3 — Medium: float() conversion loses sub-cent precision

**File:** `backend/app/api/v1/dashboard.py`, line 18  
**Symptom:** Finance team sees totals off by a few cents

The database stores `total_amount` as `NUMERIC(10, 3)` for sub-cent precision. Python's `float()` uses IEEE 754 floating point, which cannot represent all decimal values exactly.

Seed data example: `333.333 + 333.333 + 333.334 = 1000.000` (exact in NUMERIC) but `float(333.333) + float(333.333) + float(333.334)` produces a rounding error.

```python
# BROKEN
total_revenue_float = float(revenue_data['total'])
return { ..., "total_revenue": total_revenue_float, ... }

# FIX — return as string, preserve full precision
return {
    "property_id": revenue_data['property_id'],
    "total_revenue": str(revenue_data['total']),   # string, not float
    "currency": revenue_data['currency'],
    "reservations_count": revenue_data['count']
}
```

---

### BUG 4 — Medium: Timezone-naive datetimes in monthly revenue query

**File:** `backend/app/services/reservations.py`, lines 10–14  
**Symptom:** Client A's Paris-based property (`Europe/Paris`, UTC+1) has reservations mis-classified into the wrong month

`datetime(year, month, 1)` creates a timezone-naive object. The DB column is `TIMESTAMP WITH TIME ZONE`. A check-in at `2024-02-29 23:30:00+00` (UTC) is `2024-03-01 00:30:00+01` in Paris — it belongs in March, but a naive UTC comparison puts it in February.

```python
# BROKEN
start_date = datetime(year, month, 1)   # naive, treated as UTC

# FIX — fetch property timezone and localise
from zoneinfo import ZoneInfo
# (property_timezone fetched from DB: e.g. 'Europe/Paris')
tz = ZoneInfo(property_timezone)
start_date = datetime(year, month, 1, tzinfo=tz)
if month < 12:
    end_date = datetime(year, month + 1, 1, tzinfo=tz)
else:
    end_date = datetime(year + 1, 1, 1, tzinfo=tz)
```

---

### BUG 5 — Low: Frontend display rounding uses floating-point arithmetic

**File:** `frontend/src/components/RevenueSummary.tsx`, line 64  
**Symptom:** Visual rounding errors in displayed totals (compounds Bug 3)

```typescript
// BROKEN
const displayTotal = Math.round(data.total_revenue * 100) / 100;

// FIX — once backend returns string (Bug 3 fix), parse cleanly
const displayTotal = parseFloat(data.total_revenue).toFixed(2);
```

Also update the `RevenueData` TypeScript interface: `total_revenue: string` (after Bug 3 fix).

---

## Affected Files

| File | Bug | Change Type |
|---|---|---|
| `backend/app/services/cache.py` | Bug 1 | One-line fix |
| `backend/app/services/reservations.py` | Bug 2, Bug 4 | Implement function + add timezone awareness |
| `backend/app/api/v1/dashboard.py` | Bug 3 | Change float() to str() |
| `frontend/src/components/RevenueSummary.tsx` | Bug 5 | Update type + display calc |

---

## Implementation Order

Fix in this order to avoid breaking the frontend mid-deploy:

1. `cache.py` — Bug 1 (stops data leakage, zero risk)
2. `dashboard.py` — Bug 3 (return string instead of float)
3. `RevenueSummary.tsx` — Bug 5 (update to handle string + type)
4. `reservations.py` — Bug 2 (implement monthly calculation)
5. `reservations.py` — Bug 4 (add timezone support, requires property timezone lookup)

---

## Definition of Done

- [x] Start environment: `docker-compose up --build`
      → Fixed: `database_pool.py` now uses `settings.database_url`; `supabase_connection_pool.py` skips gracefully when `SUPABASE_URL` absent
- [x] Log in as Ocean Rentals (`ocean@propertyflow.com` / `client_b_2024`), view `prop-001` — sees their own revenue only
      → Fixed: cache key is now `revenue:{property_id}:{tenant_id}`
      → E2E test: `test_e2e_dashboard.py::TestCacheTenantIsolation::test_ocean_gets_own_revenue_for_prop_001`
- [x] Log in as Sunset Properties (`sunset@propertyflow.com` / `client_a_2024`), view `prop-001` — sees their own revenue only
      → Fixed: same cache key fix
      → E2E test: `test_e2e_dashboard.py::TestCacheTenantIsolation::test_sunset_gets_own_revenue_for_prop_001`
- [x] Redis cache cleared between tests to confirm isolation
      → E2E test: `test_e2e_dashboard.py::TestCacheTenantIsolation::test_tenants_see_different_totals_for_same_property_id`
      → Unit test: `test_cache_isolation.py::TestCacheIsolation::test_tenant_a_and_b_use_different_cache_keys`
- [x] `prop-001` (tenant-a) total = **2250.000** (1250.000 + 333.333 + 333.333 + 333.334) — no float drift
      → Fixed: `dashboard.py` returns `str(revenue_data['total'])` instead of `float(...)`
      → E2E test: `test_e2e_dashboard.py::TestRevenuePrecision::test_prop_001_tenant_a_total_is_exact`
      → Unit test: `test_dashboard_precision.py::TestDecimalArithmetic::test_prop_001_tenant_a_exact_sum`
- [x] March monthly revenue for `prop-001` (tenant-a) = **2250.000** (not 0)
      → Fixed: `calculate_monthly_revenue` now queries the database
      → Unit test: `test_monthly_revenue.py::TestMonthlyRevenueNotPlaceholder::test_monthly_revenue_returns_db_value_not_zero`
- [x] Reservation `res-tz-1` (`2024-02-29 23:30:00+00`) counted in **March** for Europe/Paris, not February
      → Fixed: month boundaries use `datetime(..., tzinfo=ZoneInfo(property_timezone))`
      → Unit test: `test_monthly_revenue.py::TestTimezoneAwareBoundaries::test_aware_datetime_correctly_includes_res_tz_1_in_march`
- [x] Displayed totals match DB values exactly — finance confirms no cent discrepancies
      → Fixed: frontend uses `parseFloat(string).toFixed(2)` instead of `Math.round(float * 100) / 100`
      → Frontend test: `RevenueSummary.test.tsx — does not display 999.99 for a value that is exactly 1000.000`
- [x] Both client accounts can only see their own data after multiple refreshes
      → E2E test: `test_e2e_dashboard.py::TestCacheTenantIsolation::test_second_request_uses_cache_and_still_isolates`

## How to Run Tests

### Backend unit tests (no server needed)
```bash
cd backend
pip install pytest pytest-asyncio httpx
pytest tests/test_cache_isolation.py tests/test_monthly_revenue.py tests/test_dashboard_precision.py -v
```

### Backend E2E tests (requires live server)
```bash
docker-compose up --build -d
cd backend
pytest tests/test_e2e_dashboard.py -v
```

### Frontend tests
```bash
cd frontend
npm install
npm test
```

---

## Notes

- Do NOT rebuild the system — all fixes are targeted line-level changes in existing files
- The mock data fallback in `reservations.py:92-109` is property-specific and tenant-agnostic — it only fires if the DB is unavailable, which is acceptable for dev but should be noted
- `SecureAPIClient.generateCacheKey` in the frontend already includes tenant isolation correctly — the backend Redis cache is where the isolation was missing
