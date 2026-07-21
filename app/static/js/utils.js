/**
 * utils.js — Shared helpers for the ProductPriceSync dashboard.
 */

/** Show a toast notification (auto-hides after 3 seconds). */
export function showToast(message, type = 'info') {
    const toast = document.getElementById('toast');
    if (!toast) return;
    toast.className = `toast toast-${type} show`;
    toast.textContent = message;
    setTimeout(() => toast.classList.remove('show'), 3000);
}

/** Format a duration in seconds to a human-readable string. */
export function formatDuration(seconds) {
    if (seconds == null) return '-';
    if (seconds < 60) return `${seconds.toFixed(1)}s`;
    return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
}

/** Format an ISO timestamp as a consistent 12-hour clock with AM/PM. */
export function formatTime(isoStr) {
    if (!isoStr) return '-';
    const d = new Date(isoStr);
    return d.toLocaleTimeString('en-US', {
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: true,
    });
}

/** Format an ISO timestamp to full locale date + time. */
export function formatDateTime(isoStr) {
    if (!isoStr) return '-';
    const d = new Date(isoStr);
    return d.toLocaleString();
}

/** Truncate a string to max `len` characters, appending '...' if cut. */
export function truncate(str, len = 60) {
    if (!str) return '-';
    return str.length > len ? str.substring(0, len) + '...' : str;
}

/** Run a countdown timer every second for the next-run display. */
let _nextRunTimestamp = null;
let _countdownInterval = null;

export function setNextRunTimestamp(isoStr) {
    _nextRunTimestamp = isoStr ? new Date(isoStr) : null;
}

export function startCountdown(elId = 'nextRunCountdown', subElId = 'nextRunTime') {
    if (_countdownInterval) clearInterval(_countdownInterval);
    _countdownInterval = setInterval(() => {
        updateCountdown(elId, subElId);
    }, 1000);
    updateCountdown(elId, subElId);
}

function updateCountdown(elId, subElId) {
    const el = document.getElementById(elId);
    const subEl = subElId ? document.getElementById(subElId) : null;
    if (!el) return;

    if (!_nextRunTimestamp) {
        el.textContent = '--';
        if (subEl) subEl.textContent = 'Not scheduled';
        return;
    }

    const now = new Date();
    const diff = _nextRunTimestamp - now;

    if (diff <= 0) {
        el.textContent = 'Starting...';
        if (subEl) subEl.textContent = formatTime(_nextRunTimestamp.toISOString());
        return;
    }

    const hrs = Math.floor(diff / 3600000);
    const mins = Math.floor((diff % 3600000) / 60000);
    const secs = Math.floor((diff % 60000) / 1000);

    let display = '';
    if (hrs > 0) display += `${hrs}h `;
    if (mins > 0 || hrs > 0) display += `${mins}m `;
    display += `${secs}s`;

    el.textContent = display;
    if (subEl) subEl.textContent = formatTime(_nextRunTimestamp.toISOString());
}
