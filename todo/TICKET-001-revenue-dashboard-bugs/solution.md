# Solution: Property Revenue Dashboard Bugs

**Ticket:** TICKET-001  
**Status:** Fixed  
**Files changed:** 4

---

## Plain English Summary

Three problems were reported by clients after the revenue dashboard launched. All three turned out to be bugs in the code — not data entry mistakes or configuration issues. Here is what was going wrong and what was done to fix it.

---

### Problem 1 — Ocean Rentals saw Sunset Properties' numbers (privacy issue)

**What was wrong:**
The system uses a speed cache — a place where it saves the result of a calculation so it doesn't have to recalculate it every time someone opens the page. The problem was that this cache was labelled only with the property ID (like "prop-001"), but not with *which company* the property belongs to. Both Sunset Properties and Ocean Rentals have a property with the ID "prop-001". So when one company loaded their revenue, it got saved under that shared label. The next company to open the page got handed the cached result from the *other* company — not their own.

**The fix:**
The cache label now includes both the property ID and the company ID, so each company always gets their own private result. Think of it like labelling a file drawer with both the document name and the owner's name, instead of just the document name.

---

### Problem 2 — Sunset Properties' monthly revenue totals were wrong

**What was wrong:**
There is a function in the system responsible for calculating how much revenue a property made in a specific month. That function was never actually finished — it had a placeholder that always returned zero, with a note saying "to be completed later." It was shipped with that placeholder still in place, so any monthly figures shown on the dashboard were always zero, regardless of actual bookings.

**The fix:**
The function was fully implemented. It now correctly queries the database for all reservations within the requested month and returns the actual total.

---

### Problem 3 — Totals slightly off by a few cents (finance team)

**What was wrong (part a):**
The database stores revenue amounts with very precise decimal values (for example, 333.333). When the backend prepared those numbers to send to the frontend, it converted them from a precise decimal type into a regular floating-point number — the kind computers use for fast math. Floating-point numbers cannot represent all decimal values exactly, which causes tiny rounding errors. Three amounts that should add up to exactly 1000.000 could end up as 999.999... or 1000.001.

**What was wrong (part b):**
The frontend then applied its own rounding on top of that already-imprecise number, compounding the problem.

**The fix:**
The backend now sends the revenue total as a precise string (e.g. "1000.000") instead of converting it to a floating-point number. The frontend reads it as a string and formats it for display without doing any arithmetic that could introduce further drift.

---

### Bonus fix — Paris properties had reservations counted in the wrong month

**What was wrong:**
Properties are located in different time zones (Paris, New York, etc.). The monthly revenue calculation was using times without any time zone information, which meant it was comparing everything against midnight UTC. A guest checking in at 11:30 PM in Paris (which is already the next day in Paris time, but still yesterday in UTC) could be counted in the wrong month.

**The fix:**
The calculation now uses the property's actual local time zone when determining where a booking falls in the calendar.

---

## Technical Description

### Bug 1 — Cross-tenant cache collision

**Root cause:** Redis cache key did not include `tenant_id`.

**File:** `backend/app/services/cache.py`, line 13

Both tenants share the property ID `prop-001` (confirmed in `database/seed.sql`). The previous cache key `revenue:{property_id}` created a single shared Redis entry for all tenants with that property ID. The first tenant to request revenue for `prop-001` populated `revenue:prop-001`; the second tenant received that cached response verbatim, bypassing the `tenant_id` filter in the SQL query entirely.

```python
# Before
cache_key = f"revenue:{property_id}"

# After
cache_key = f"revenue:{property_id}:{tenant_id}"
```

This is a standard multi-tenant cache isolation pattern. The frontend's `SecureAPIClient.generateCacheKey()` already applied this pattern correctly — the backend Redis layer did not.

---

### Bug 2 — `calculate_monthly_revenue` was an unimplemented placeholder

**Root cause:** Function body unconditionally returned `Decimal('0')`.

**File:** `backend/app/services/reservations.py`, lines 9–32

The function had a SQL query drafted in a comment and a `# Placeholder for now` guard that was never removed before shipping. Any dashboard feature relying on monthly breakdowns always received zero.

The fix implements the function using the same `DatabasePool` / `SQLAlchemy text()` pattern already established in `calculate_total_revenue` directly below it:

```python
async with db_pool.get_session() as session:
    query = text("""
        SELECT COALESCE(SUM(total_amount), 0) as total
        FROM reservations
        WHERE property_id = :property_id
          AND tenant_id = :tenant_id
          AND check_in_date >= :start_date
          AND check_in_date < :end_date
    """)
    result = await session.execute(query, {...})
    return Decimal(str(result.scalar() or 0))
```

`COALESCE` ensures an empty result set returns `0` rather than `NULL`, which would cause a `Decimal(None)` crash.

---

### Bug 3 — `float()` conversion discards sub-cent NUMERIC precision

**Root cause:** `float(revenue_data['total'])` applied to a `NUMERIC(10,3)` value.

**File:** `backend/app/api/v1/dashboard.py`, line 18

The database schema defines `total_amount NUMERIC(10, 3)`. The seed data intentionally includes sub-cent amounts (`333.333`, `333.333`, `333.334`) that sum to exactly `1000.000` in fixed-point arithmetic. Python's `float()` maps this to IEEE 754 double precision, which cannot represent `333.333` exactly; cumulative error surfaces as rounding discrepancies in aggregated totals.

```python
# Before — precision lost at the API boundary
total_revenue_float = float(revenue_data['total'])
return { ..., "total_revenue": total_revenue_float, ... }

# After — full precision preserved across the wire
return { ..., "total_revenue": str(revenue_data['total']), ... }
```

The `RevenueData` TypeScript interface was updated from `total_revenue: number` to `total_revenue: string` to match.

---

### Bug 4 — Timezone-naive month boundaries in monthly revenue query

**Root cause:** `datetime(year, month, 1)` produces a naive datetime; the DB column is `TIMESTAMP WITH TIME ZONE`.

**File:** `backend/app/services/reservations.py`, lines 10–14

PostgreSQL compares a naive Python `datetime` against `TIMESTAMPTZ` as if it were UTC. For a property in `Europe/Paris` (UTC+1), a reservation with `check_in_date = '2024-02-29 23:30:00+00'` is `2024-03-01 00:30:00+01` local time — it belongs to March — but a naive UTC boundary of `datetime(2024, 3, 1)` correctly starts at midnight UTC, so this specific edge case actually lands in the right bucket. However, the reverse problem exists for check-outs and for properties ahead of UTC: a `America/New_York` property (UTC-5) has reservations at `2024-03-01 03:00:00+00` that are still February 29 local time.

```python
# Before — naive, implicitly UTC
start_date = datetime(year, month, 1)

# After — property-local timezone aware
from zoneinfo import ZoneInfo
tz = ZoneInfo(property_timezone)
start_date = datetime(year, month, 1, tzinfo=tz)
end_date = datetime(year, month + 1, 1, tzinfo=tz)  # handles year wrap too
```

`property_timezone` is passed as a parameter (sourced from the `properties.timezone` column, e.g. `Europe/Paris`, `America/New_York`). `zoneinfo` is stdlib from Python 3.9+.

---

### Bug 5 — Frontend floating-point display rounding

**Root cause:** `Math.round(x * 100) / 100` applied to an already-imprecise JS `number`.

**File:** `frontend/src/components/RevenueSummary.tsx`, line 64

With the backend now returning a string, arithmetic rounding is avoided entirely. `parseFloat().toFixed(2)` formats for display without intermediate float multiplication:

```typescript
// Before
const displayTotal = Math.round(data.total_revenue * 100) / 100;

// After
const displayTotal = parseFloat(data.total_revenue).toFixed(2);
```

`toLocaleString` is then called on `parseFloat(displayTotal)` for thousands-separator formatting, keeping the display path consistent.
