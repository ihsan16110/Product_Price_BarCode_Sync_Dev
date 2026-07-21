/**
 * api.js — Centralized HTTP client for the ProductPriceSync REST API.
 *
 * All endpoints are prefixed with /ProductSync/api/.
 * Every function returns parsed JSON or throws on non-OK responses.
 */

const BASE = '/ProductSync/api';
const API_KEY_STORAGE = 'productSyncApiKey';

export function setApiKey(apiKey) {
    const value = String(apiKey || '').trim();
    if (value) sessionStorage.setItem(API_KEY_STORAGE, value);
    else sessionStorage.removeItem(API_KEY_STORAGE);
}

export function hasApiKey() {
    return Boolean(sessionStorage.getItem(API_KEY_STORAGE));
}

export function getAuthHeaders() {
    const apiKey = sessionStorage.getItem(API_KEY_STORAGE);
    return apiKey ? { 'X-API-Key': apiKey } : {};
}

async function fetchJSON(url, options = {}) {
    const { headers = {}, ...rest } = options;
    const res = await fetch(url, {
        ...rest,
        headers: {
            'Content-Type': 'application/json',
            ...getAuthHeaders(),
            ...headers,
        },
    });
    let data;
    try {
        data = await res.json();
    } catch {
        data = {};
    }
    if (!res.ok) {
        throw new Error(data.detail || `HTTP ${res.status}`);
    }
    return data;
}

// ─── Status ──────────────────────────────────────────────────────────

export function getStatus() {
    return fetchJSON(`${BASE}/status`);
}

export function getHealth() {
    return fetchJSON(`${BASE}/health`);
}

// ─── Outlets ─────────────────────────────────────────────────────────

export function getOutlets() {
    return fetchJSON(`${BASE}/outlets`);
}

// ─── Retry Queue ─────────────────────────────────────────────────────

export function getRetries() {
    return fetchJSON(`${BASE}/retries`);
}

export function processRetries() {
    return fetchJSON(`${BASE}/retries/process-now`, { method: 'POST' });
}

export function clearRetries() {
    return fetchJSON(`${BASE}/retries`, { method: 'DELETE' });
}

// ─── Sync ────────────────────────────────────────────────────────────

export function startSync() {
    return fetchJSON(`${BASE}/sync/start`, { method: 'POST' });
}

export function stopSync() {
    return fetchJSON(`${BASE}/sync/stop`, { method: 'POST' });
}

export function syncOutlet(code) {
    return fetchJSON(`${BASE}/sync/outlet/${encodeURIComponent(code)}`, { method: 'POST' });
}

// ─── Settings / Schedule ─────────────────────────────────────────────

export function getSettings() {
    return fetchJSON(`${BASE}/settings`);
}

export function applySchedule(body) {
    return fetchJSON(`${BASE}/settings/schedule`, {
        method: 'PUT',
        body: JSON.stringify(body),
    });
}

export function pauseSchedule() {
    return fetchJSON(`${BASE}/settings/schedule/pause`, { method: 'POST' });
}

export function resumeSchedule() {
    return fetchJSON(`${BASE}/settings/schedule/resume`, { method: 'POST' });
}

// ─── Logs ────────────────────────────────────────────────────────────

export async function getLogs(date) {
    const res = await fetch(`${BASE}/logs/archive/${encodeURIComponent(date)}`, {
        headers: getAuthHeaders(),
    });
    if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || `HTTP ${res.status}`);
    }
    return res.text();
}

export async function openLogStream(signal) {
    const res = await fetch(`${BASE}/logs/stream`, {
        headers: getAuthHeaders(),
        signal,
    });
    if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || `HTTP ${res.status}`);
    }
    if (!res.body) throw new Error('Streaming response is unavailable');
    return res.body;
}

// ─── Price Changes ───────────────────────────────────────────────────

export function getPriceChanges(params = {}) {
    const qs = new URLSearchParams();
    qs.set('days', params.days ?? '7');
    qs.set('limit', params.limit ?? '100');
    if (params.outlet_code) qs.set('outlet_code', params.outlet_code);
    const query = qs.toString();
    return fetchJSON(`${BASE}/logs/price-changes?${query}`);
}

// ─── Price Change Cleanup ────────────────────────────────────────────

export function triggerCleanup(body = {}) {
    return fetchJSON(`${BASE}/cleanup/price-changes`, {
        method: 'POST',
        body: JSON.stringify(body),
    });
}
