/**
 * Unit tests for app/static/js/api.js
 *
 * Tests cover:
 * - Each endpoint makes the correct HTTP request
 * - Error handling for non-OK responses
 * - Query parameter construction
 * - Text/JSON response parsing
 */

import * as api from '../../app/static/js/api.js';

const BASE = '/ProductSync/api';

// =========================================================================
// Helper: mock a single fetch response
// =========================================================================

function mockFetch(status, body, ok = true) {
    const jsonBody = typeof body === 'string' ? body : JSON.stringify(body);
    global.fetch = jest.fn().mockResolvedValue({
        ok,
        status,
        json: jest.fn().mockResolvedValue(typeof body === 'string' ? JSON.parse(body) : body),
        text: jest.fn().mockResolvedValue(jsonBody),
    });
}

function mockFetchError(message = 'Network error') {
    global.fetch = jest.fn().mockRejectedValue(new Error(message));
}

// =========================================================================
// Status
// =========================================================================

describe('getStatus', () => {
    afterEach(() => jest.restoreAllMocks());

    test('fetches /status and returns JSON', async () => {
        const fake = { service: 'ProductPriceSync', uptime_seconds: 3600 };
        mockFetch(200, fake);
        const result = await api.getStatus();
        expect(global.fetch).toHaveBeenCalledWith(`${BASE}/status`, expect.any(Object));
        expect(result.service).toBe('ProductPriceSync');
    });

    test('throws on non-OK response', async () => {
        mockFetch(500, { detail: 'Server error' }, false);
        await expect(api.getStatus()).rejects.toThrow('Server error');
    });
});

// =========================================================================
// Sync
// =========================================================================

describe('startSync', () => {
    afterEach(() => jest.restoreAllMocks());

    test('POSTs to /sync/start', async () => {
        mockFetch(200, { message: 'Sync started', trigger: 'manual' });
        const result = await api.startSync();
        expect(global.fetch).toHaveBeenCalledWith(
            `${BASE}/sync/start`,
            expect.objectContaining({ method: 'POST' })
        );
        expect(result.message).toBe('Sync started');
    });
});

describe('stopSync', () => {
    afterEach(() => jest.restoreAllMocks());

    test('POSTs to /sync/stop', async () => {
        mockFetch(200, { status: 'stopping', message: 'Cancellation requested' });
        const result = await api.stopSync();
        expect(global.fetch).toHaveBeenCalledWith(
            `${BASE}/sync/stop`,
            expect.objectContaining({ method: 'POST' })
        );
        expect(result.status).toBe('stopping');
    });
});

describe('syncOutlet', () => {
    afterEach(() => jest.restoreAllMocks());

    test('POSTs to /sync/outlet/{code}', async () => {
        mockFetch(200, { outlet_code: 'B004', status: 'Success' });
        const result = await api.syncOutlet('B004');
        expect(global.fetch).toHaveBeenCalledWith(
            `${BASE}/sync/outlet/B004`,
            expect.objectContaining({ method: 'POST' })
        );
        expect(result.status).toBe('Success');
    });

    test('encodes outlet code in URL', async () => {
        mockFetch(200, { outlet_code: 'B 004', status: 'Success' });
        await api.syncOutlet('B 004');
        expect(global.fetch).toHaveBeenCalledWith(
            `${BASE}/sync/outlet/B%20004`,
            expect.any(Object)
        );
    });
});

// =========================================================================
// Retry Queue
// =========================================================================

describe('retries', () => {
    afterEach(() => jest.restoreAllMocks());

    test('getRetries fetches /retries', async () => {
        mockFetch(200, { entries: [], size: 0 });
        const result = await api.getRetries();
        expect(global.fetch).toHaveBeenCalledWith(`${BASE}/retries`, expect.any(Object));
        expect(result.size).toBe(0);
    });

    test('processRetries POSTs to /retries/process-now', async () => {
        mockFetch(200, { message: 'Processing 3 retries', processed: 3 });
        const result = await api.processRetries();
        expect(global.fetch).toHaveBeenCalledWith(
            `${BASE}/retries/process-now`,
            expect.objectContaining({ method: 'POST' })
        );
        expect(result.processed).toBe(3);
    });

    test('clearRetries DELETEs /retries', async () => {
        mockFetch(200, { message: 'Cleared', removed: 5 });
        const result = await api.clearRetries();
        expect(global.fetch).toHaveBeenCalledWith(
            `${BASE}/retries`,
            expect.objectContaining({ method: 'DELETE' })
        );
        expect(result.removed).toBe(5);
    });
});

// =========================================================================
// Settings / Schedule
// =========================================================================

describe('applySchedule', () => {
    afterEach(() => jest.restoreAllMocks());

    test('PUTs schedule body to /settings/schedule', async () => {
        const body = { mode: 'visual', interval_minutes: 15 };
        mockFetch(200, { message: 'Applied' });
        await api.applySchedule(body);
        expect(global.fetch).toHaveBeenCalledWith(
            `${BASE}/settings/schedule`,
            expect.objectContaining({
                method: 'PUT',
                body: JSON.stringify(body),
            })
        );
    });
});

describe('schedule controls', () => {
    afterEach(() => jest.restoreAllMocks());

    test('POSTs to /settings/schedule/pause', async () => {
        mockFetch(200, { enabled: false });
        const result = await api.pauseSchedule();
        expect(global.fetch).toHaveBeenCalledWith(
            `${BASE}/settings/schedule/pause`,
            expect.objectContaining({ method: 'POST' })
        );
        expect(result.enabled).toBe(false);
    });

    test('POSTs to /settings/schedule/resume', async () => {
        mockFetch(200, { enabled: true });
        const result = await api.resumeSchedule();
        expect(global.fetch).toHaveBeenCalledWith(
            `${BASE}/settings/schedule/resume`,
            expect.objectContaining({ method: 'POST' })
        );
        expect(result.enabled).toBe(true);
    });
});

// =========================================================================
// Logs
// =========================================================================

describe('getLogs', () => {
    afterEach(() => {
        jest.restoreAllMocks();
        sessionStorage.clear();
    });

    test('fetches the unambiguous archive route and returns text', async () => {
        global.fetch = jest.fn().mockResolvedValue({
            ok: true,
            status: 200,
            text: jest.fn().mockResolvedValue('[log line 1]\n[log line 2]'),
        });
        const result = await api.getLogs('2026-02-24');
        expect(global.fetch).toHaveBeenCalledWith(
            `${BASE}/logs/archive/2026-02-24`,
            { headers: {} },
        );
        expect(result).toBe('[log line 1]\n[log line 2]');
    });

    test('sends a tab-scoped API key to protected endpoints', async () => {
        api.setApiKey('test-admin-key');
        global.fetch = jest.fn().mockResolvedValue({
            ok: true,
            status: 200,
            text: jest.fn().mockResolvedValue('secured log'),
        });

        await api.getLogs('2026-02-24');

        expect(global.fetch).toHaveBeenCalledWith(
            `${BASE}/logs/archive/2026-02-24`,
            { headers: { 'X-API-Key': 'test-admin-key' } },
        );
    });

    test('throws on non-OK response', async () => {
        global.fetch = jest.fn().mockResolvedValue({
            ok: false,
            status: 404,
            json: jest.fn().mockResolvedValue({ detail: 'Log file not found' }),
        });
        await expect(api.getLogs('2026-01-01')).rejects.toThrow('Log file not found');
    });
});

// =========================================================================
// Price Changes
// =========================================================================

describe('getPriceChanges', () => {
    afterEach(() => jest.restoreAllMocks());

    test('fetches default params (7 days, 100 limit)', async () => {
        mockFetch(200, { changes: [], count: 0 });
        await api.getPriceChanges();
        expect(global.fetch).toHaveBeenCalledWith(
            `${BASE}/logs/price-changes?days=7&limit=100`,
            expect.any(Object)
        );
    });

    test('includes outlet_code filter when provided', async () => {
        mockFetch(200, { changes: [], count: 0 });
        await api.getPriceChanges({ outlet_code: 'B004', days: 1, limit: 50 });
        const url = global.fetch.mock.calls[0][0];
        expect(url).toContain('outlet_code=B004');
        expect(url).toContain('days=1');
        expect(url).toContain('limit=50');
    });

    test('omits outlet_code from query when not provided', async () => {
        mockFetch(200, { changes: [], count: 0 });
        await api.getPriceChanges({ days: 7 });
        const url = global.fetch.mock.calls[0][0];
        expect(url).not.toContain('outlet_code');
    });
});

// =========================================================================
// Cleanup
// =========================================================================

describe('triggerCleanup', () => {
    afterEach(() => jest.restoreAllMocks());

    test('POSTs to /cleanup/price-changes with body', async () => {
        const body = { retention_days: 90, batch_size: 5000 };
        mockFetch(200, { status: 'ok', deleted: 1500 });
        const result = await api.triggerCleanup(body);
        expect(global.fetch).toHaveBeenCalledWith(
            `${BASE}/cleanup/price-changes`,
            expect.objectContaining({
                method: 'POST',
                body: JSON.stringify(body),
            })
        );
        expect(result.deleted).toBe(1500);
    });

    test('sends empty object when called without args', async () => {
        mockFetch(200, { status: 'ok', deleted: 0 });
        await api.triggerCleanup();
        expect(global.fetch).toHaveBeenCalledWith(
            `${BASE}/cleanup/price-changes`,
            expect.objectContaining({
                body: JSON.stringify({}),
            })
        );
    });
});

// =========================================================================
// Error handling
// =========================================================================

describe('network errors', () => {
    afterEach(() => jest.restoreAllMocks());

    test('throws on network failure', async () => {
        mockFetchError('Failed to fetch');
        await expect(api.getStatus()).rejects.toThrow('Failed to fetch');
    });

    test('throws on non-JSON error with fallback message', async () => {
        global.fetch = jest.fn().mockResolvedValue({
            ok: false,
            status: 503,
            json: jest.fn().mockRejectedValue(new Error('invalid json')),
        });
        await expect(api.getStatus()).rejects.toThrow('HTTP 503');
    });
});
