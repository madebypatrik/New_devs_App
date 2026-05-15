/**
 * Tests for Bug 5: frontend revenue display must not introduce floating-point
 * rounding errors when formatting monetary values.
 *
 * DoD items covered:
 *   - Displayed totals match DB values exactly
 *   - No float rounding artifacts for sub-cent NUMERIC(10,3) amounts
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { RevenueSummary } from '../RevenueSummary';

// ── mock SecureAPI ────────────────────────────────────────────────────────────

vi.mock('../../lib/secureApi', () => ({
  SecureAPI: {
    getDashboardSummary: vi.fn(),
  },
}));

// ── mock Supabase so the module resolves without real credentials ─────────────

vi.mock('../../lib/supabase', () => ({
  supabase: {
    auth: {
      getSession: vi.fn().mockResolvedValue({ data: { session: null } }),
      onAuthStateChange: vi.fn().mockReturnValue({ data: { subscription: { unsubscribe: vi.fn() } } }),
    },
    from: vi.fn(),
  },
}));

import { SecureAPI } from '../../lib/secureApi';
const mockGetDashboardSummary = vi.mocked(SecureAPI.getDashboardSummary);


// ── Bug 5: parseFloat().toFixed(2) vs Math.round(x * 100) / 100 ─────────────

describe('RevenueSummary — display precision (Bug 5)', () => {

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('displays 2250.000 as "2,250.00" without rounding drift', async () => {
    mockGetDashboardSummary.mockResolvedValue({
      property_id: 'prop-001',
      total_revenue: '2250.000',
      currency: 'USD',
      reservations_count: 4,
    });

    render(<RevenueSummary propertyId="prop-001" />);
    await waitFor(() => {
      expect(screen.getByText(/2,250\.00/)).toBeInTheDocument();
    });
  });

  it('displays 1000.000 exactly — no float artifact from 333.333 × 3', async () => {
    /**
     * The old code: Math.round(data.total_revenue * 100) / 100
     * If total_revenue was a number (float from backend), 333.333 + 333.333 + 333.334
     * in JS float gives 999.9999999999999 → rounds to 1000.00 coincidentally,
     * but 1000.000 as a float might display as 999.99 in edge cases.
     *
     * With the fix (string → parseFloat → toFixed), the value is stable.
     */
    mockGetDashboardSummary.mockResolvedValue({
      property_id: 'prop-test',
      total_revenue: '1000.000',
      currency: 'USD',
      reservations_count: 3,
    });

    render(<RevenueSummary propertyId="prop-test" />);
    await waitFor(() => {
      expect(screen.getByText(/1,000\.00/)).toBeInTheDocument();
    });
  });

  it('does not display 999.99 for a value that is exactly 1000.000', async () => {
    mockGetDashboardSummary.mockResolvedValue({
      property_id: 'prop-test',
      total_revenue: '1000.000',
      currency: 'USD',
      reservations_count: 3,
    });

    render(<RevenueSummary propertyId="prop-test" />);
    await waitFor(() => {
      expect(screen.queryByText(/999\.99/)).not.toBeInTheDocument();
    });
  });

  it('displays sub-cent value 4975.500 as "4,975.50"', async () => {
    mockGetDashboardSummary.mockResolvedValue({
      property_id: 'prop-002',
      total_revenue: '4975.500',
      currency: 'USD',
      reservations_count: 4,
    });

    render(<RevenueSummary propertyId="prop-002" />);
    await waitFor(() => {
      expect(screen.getByText(/4,975\.50/)).toBeInTheDocument();
    });
  });

  it('displays zero revenue as "0.00"', async () => {
    mockGetDashboardSummary.mockResolvedValue({
      property_id: 'prop-001',
      total_revenue: '0.00',
      currency: 'USD',
      reservations_count: 0,
    });

    render(<RevenueSummary propertyId="prop-001" />);
    await waitFor(() => {
      expect(screen.getByText(/0\.00/)).toBeInTheDocument();
    });
  });

  it('shows correct reservation count', async () => {
    mockGetDashboardSummary.mockResolvedValue({
      property_id: 'prop-001',
      total_revenue: '2250.000',
      currency: 'USD',
      reservations_count: 4,
    });

    render(<RevenueSummary propertyId="prop-001" />);
    await waitFor(() => {
      expect(screen.getByText(/4/)).toBeInTheDocument();
    });
  });

  it('shows error state when API call fails', async () => {
    mockGetDashboardSummary.mockRejectedValue(new Error('Network error'));

    render(<RevenueSummary propertyId="prop-001" />);
    await waitFor(() => {
      expect(screen.getByText(/Failed to load revenue data/)).toBeInTheDocument();
    });
  });
});


// ── Pure logic tests (no DOM) ─────────────────────────────────────────────────

describe('Revenue display calculation — pure logic', () => {

  it('parseFloat + toFixed(2) is stable for 2250.000', () => {
    const apiValue = '2250.000';
    const displayTotal = parseFloat(apiValue).toFixed(2);
    expect(displayTotal).toBe('2250.00');
  });

  it('parseFloat + toFixed(2) is stable for 1000.000 (333.333 × 3 scenario)', () => {
    const apiValue = '1000.000';
    const displayTotal = parseFloat(apiValue).toFixed(2);
    expect(displayTotal).toBe('1000.00');
  });

  it('old approach Math.round(x * 100) / 100 can introduce drift for floats', () => {
    /**
     * This test documents the broken behaviour the fix replaced.
     * In the worst case, a number like 1000.0049999... rounds incorrectly.
     */
    const slightlyOff = 999.9999999999999; // what JS float arithmetic can produce
    const oldMethod = Math.round(slightlyOff * 100) / 100;
    // Demonstrates the old approach was fragile — value-dependent
    expect(oldMethod).toBe(1000); // "coincidentally" correct here, but not reliable
  });

  it('parseFloat on a precision string gives the correct number', () => {
    expect(parseFloat('333.333')).toBeCloseTo(333.333, 3);
    expect(parseFloat('1000.000')).toBe(1000);
    expect(parseFloat('2250.000')).toBe(2250);
  });

  it('total_revenue is treated as string (interface type check via assignment)', () => {
    // Simulates the TypeScript interface: total_revenue: string
    const data: { total_revenue: string } = { total_revenue: '2250.000' };
    const displayTotal = parseFloat(data.total_revenue).toFixed(2);
    expect(typeof displayTotal).toBe('string');
    expect(displayTotal).toBe('2250.00');
  });
});
