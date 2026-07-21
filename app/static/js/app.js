/**
 * app.js — Main orchestrator for the ProductPriceSync Dashboard.
 *
 * Initializes all components, starts the polling loop, and wires
 * global click handlers for inline onclick attributes in the HTML.
 *
 * Imports:
 *   status-cards   – refresh() every poll cycle
 *   outlets        – refresh() every poll cycle
 *   retries        – refresh() every poll cycle
 *   price-changes  – refresh() every poll cycle
 *   schedule       – init() once (responds to user actions)
 *   logs           – init() once (SSE + archived log viewer)
 *   cleanup        – init() once (responds to user actions)
 */

import {
    hasApiKey,
    setApiKey,
    startSync,
    stopSync,
} from './api.js';
import { startCountdown, showToast } from './utils.js';

import { init as initStatus,   refresh as refreshStatus }   from './components/status-cards.js';
import { init as initOutlets,  refresh as refreshOutlets }  from './components/outlets.js';
import { init as initRetries,  refresh as refreshRetries }  from './components/retries.js';
import { init as initSchedule }                              from './components/schedule.js';
import { init as initLogs, connectLiveLogs }                 from './components/logs.js';
import { init as initPriceChanges, refresh as refreshPriceChanges } from './components/price-changes.js';
import { init as initCleanup }                               from './components/cleanup.js';
import { init as initSingleOutlet }                          from './components/single-outlet.js';

let pollInterval = null;
let fullSyncPreviouslyFocusedElement = null;
let stopSyncPreviouslyFocusedElement = null;

function openFullSyncConfirmation() {
    const modal = document.getElementById('fullSyncConfirmModal');
    if (!modal) return;
    fullSyncPreviouslyFocusedElement = document.activeElement;
    modal.hidden = false;
    document.getElementById('btnConfirmFullSync')?.focus();
}

function closeFullSyncConfirmation() {
    const modal = document.getElementById('fullSyncConfirmModal');
    if (modal) modal.hidden = true;
    fullSyncPreviouslyFocusedElement?.focus();
    fullSyncPreviouslyFocusedElement = null;
}

function confirmFullSync() {
    closeFullSyncConfirmation();
    handleStartSync();
}

function openStopSyncConfirmation() {
    const modal = document.getElementById('stopSyncConfirmModal');
    if (!modal) return;
    stopSyncPreviouslyFocusedElement = document.activeElement;
    modal.hidden = false;
    document.getElementById('btnConfirmStopSync')?.focus();
}

function closeStopSyncConfirmation() {
    const modal = document.getElementById('stopSyncConfirmModal');
    if (modal) modal.hidden = true;
    stopSyncPreviouslyFocusedElement?.focus();
    stopSyncPreviouslyFocusedElement = null;
}

function confirmStopSync() {
    closeStopSyncConfirmation();
    handleStopSync();
}

async function handleStartSync() {
    try {
        await startSync();
        showToast('Sync cycle started', 'success');
    } catch (e) {
        showToast(e.message || 'Failed to start sync', 'error');
    }
}

async function handleStopSync() {
    try {
        const data = await stopSync();
        showToast(data.message, data.status === 'stopping' ? 'info' : 'success');
    } catch (e) {
        showToast(e.message || 'Failed to stop sync', 'error');
    }
}

function connectApi() {
    const input = document.getElementById('apiKeyInput');
    const status = document.getElementById('authStatus');
    setApiKey(input ? input.value : '');
    if (status) status.textContent = hasApiKey() ? 'Key loaded for this tab' : 'Not authenticated';
    refreshStatus();
    refreshOutlets();
    refreshRetries();
    refreshPriceChanges();
    connectLiveLogs();
}

// ─── Polling ─────────────────────────────────────────────────────────

function startPolling() {
    // Initial fetch
    refreshStatus();
    refreshOutlets();
    refreshRetries();
    refreshPriceChanges();

    // Countdown ticks every 1s (handled inside utils)
    startCountdown();

    // Poll every 5 seconds
    pollInterval = setInterval(() => {
        refreshStatus();
        refreshOutlets();
        refreshRetries();
        refreshPriceChanges();
    }, 5000);
}

// ─── Boot ────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
    // Initialize all components (one-time setup)
    initStatus();
    initOutlets();
    initRetries();
    initSchedule();
    initLogs();
    initPriceChanges();
    initCleanup();
    initSingleOutlet();

    document.getElementById('btnStartSync')?.addEventListener('click', openFullSyncConfirmation);
    document.getElementById('btnConfirmFullSync')?.addEventListener('click', confirmFullSync);
    document.getElementById('btnCancelFullSync')?.addEventListener('click', closeFullSyncConfirmation);
    document.getElementById('fullSyncConfirmModal')?.addEventListener('click', event => {
        if (event.target === event.currentTarget) closeFullSyncConfirmation();
    });
    document.addEventListener('keydown', event => {
        const modal = document.getElementById('fullSyncConfirmModal');
        if (event.key === 'Escape' && modal && !modal.hidden) closeFullSyncConfirmation();
        const stopModal = document.getElementById('stopSyncConfirmModal');
        if (event.key === 'Escape' && stopModal && !stopModal.hidden) closeStopSyncConfirmation();
    });
    document.getElementById('btnStopSync')?.addEventListener('click', openStopSyncConfirmation);
    document.getElementById('btnConfirmStopSync')?.addEventListener('click', confirmStopSync);
    document.getElementById('btnCancelStopSync')?.addEventListener('click', closeStopSyncConfirmation);
    document.getElementById('stopSyncConfirmModal')?.addEventListener('click', event => {
        if (event.target === event.currentTarget) closeStopSyncConfirmation();
    });
    document.getElementById('btnConnectApi')?.addEventListener('click', connectApi);
    document.getElementById('apiKeyInput')?.addEventListener('keydown', event => {
        if (event.key === 'Enter') connectApi();
    });
    const authStatus = document.getElementById('authStatus');
    if (authStatus && hasApiKey()) authStatus.textContent = 'Key loaded for this tab';

    // Start polling
    startPolling();
});
