/**
 * Unit tests for app/static/js/utils.js
 *
 * Tests cover:
 * - formatDuration — seconds to human-readable
 * - formatTime — ISO string to locale time
 * - formatDateTime — ISO string to locale date-time
 * - truncate — string truncation
 * - showToast — DOM-based toast notification
 */

import { formatDuration, formatTime, formatDateTime, truncate, showToast } from '../../app/static/js/utils.js';

// =========================================================================
// formatDuration
// =========================================================================

describe('formatDuration', () => {
    test('returns "-" for null/undefined input', () => {
        expect(formatDuration(null)).toBe('-');
        expect(formatDuration(undefined)).toBe('-');
    });

    test('formats seconds under 60 using toFixed(1)', () => {
        // JavaScript's toFixed uses round-half-to-even (banker's rounding)
        expect(formatDuration(8.45)).toBe('8.4s');
        expect(formatDuration(8.5)).toBe('8.5s');
        expect(formatDuration(0)).toBe('0.0s');
        expect(formatDuration(59)).toBe('59.0s');
    });

    test('formats minutes and seconds for >= 60', () => {
        expect(formatDuration(60)).toBe('1m 0s');
        expect(formatDuration(90)).toBe('1m 30s');
        expect(formatDuration(3600)).toBe('60m 0s');
        expect(formatDuration(3661)).toBe('61m 1s');
    });

    test('handles edge values', () => {
        expect(formatDuration(59)).toBe('59.0s');
        expect(formatDuration(60)).toBe('1m 0s');
        expect(formatDuration(119)).toBe('1m 59s');
        expect(formatDuration(120)).toBe('2m 0s');
    });
});

// =========================================================================
// formatTime
// =========================================================================

describe('formatTime', () => {
    test('returns "-" for null/undefined/empty input', () => {
        expect(formatTime(null)).toBe('-');
        expect(formatTime(undefined)).toBe('-');
        expect(formatTime('')).toBe('-');
    });

    test('returns a locale time string for valid ISO input', () => {
        const result = formatTime('2026-02-24T14:35:22');
        // Can't assert exact string since locale differs, but it should
        // contain the time portion
        expect(result).not.toBe('-');
        expect(result).toMatch(/\b(?:AM|PM)$/);
        expect(typeof result).toBe('string');
        expect(result.length).toBeGreaterThan(0);
    });

    test('handles date-only strings', () => {
        const result = formatTime('2026-02-24');
        expect(result).not.toBe('-');
        expect(typeof result).toBe('string');
    });
});

// =========================================================================
// formatDateTime
// =========================================================================

describe('formatDateTime', () => {
    test('returns "-" for null/undefined input', () => {
        expect(formatDateTime(null)).toBe('-');
        expect(formatDateTime(undefined)).toBe('-');
    });

    test('returns a locale date-time string for valid input', () => {
        const result = formatDateTime('2026-02-24T14:35:22');
        expect(result).not.toBe('-');
        expect(typeof result).toBe('string');
        expect(result.length).toBeGreaterThan(5);
    });
});

// =========================================================================
// truncate
// =========================================================================

describe('truncate', () => {
    test('returns "-" for null/undefined/empty input', () => {
        expect(truncate(null)).toBe('-');
        expect(truncate(undefined)).toBe('-');
        expect(truncate('')).toBe('-');
    });

    test('returns full string when shorter than max length', () => {
        expect(truncate('hello', 10)).toBe('hello');
        expect(truncate('hello', 5)).toBe('hello');
    });

    test('truncates with ellipsis appended after truncation', () => {
        // Function returns substring(0, len) + '...' resulting in len+3 chars
        expect(truncate('hello world', 5)).toBe('hello...');
        expect(truncate('connection timeout after 10 seconds', 20)).toBe('connection timeout a...');
    });

    test('uses default length of 60', () => {
        const short = 'short string';
        const long = 'a'.repeat(100);
        expect(truncate(short)).toBe(short);
        expect(truncate(long)).toBe('a'.repeat(60) + '...');
    });

    test('handles very short max lengths', () => {
        expect(truncate('hello', 3)).toBe('hel...');
        expect(truncate('hi', 0)).toBe('...');
    });
});

// =========================================================================
// showToast (DOM environment via jsdom)
// =========================================================================

describe('showToast', () => {
    beforeEach(() => {
        // Set up a toast element in jsdom
        document.body.innerHTML = '<div class="toast" id="toast"></div>';
        jest.useFakeTimers();
    });

    afterEach(() => {
        jest.useRealTimers();
    });

    test('adds show class and sets text content', () => {
        showToast('Hello, world!', 'success');
        const toast = document.getElementById('toast');
        expect(toast.textContent).toBe('Hello, world!');
        expect(toast.className).toContain('toast-success');
        expect(toast.className).toContain('show');
    });

    test('defaults to info type', () => {
        showToast('Info message');
        const toast = document.getElementById('toast');
        expect(toast.className).toContain('toast-info');
    });

    test('removes show class after timeout', () => {
        showToast('Temporary', 'error');
        const toast = document.getElementById('toast');
        expect(toast.className).toContain('show');

        // Fast-forward past the timeout
        jest.advanceTimersByTime(3000);
        expect(toast.className).not.toContain('show');
    });

    test('does not throw when toast element is missing', () => {
        document.body.innerHTML = '';
        expect(() => showToast('orphan')).not.toThrow();
    });
});
