/** Single-outlet production test control. */

import { syncOutlet } from '../api.js';
import { showToast } from '../utils.js';
import { refresh as refreshOutlets } from './outlets.js';

const OUTLET_CODE_PATTERN = /^[A-Z0-9_-]+$/;
let pendingOutletCode = null;
let previouslyFocusedElement = null;

export function init() {
    const input = document.getElementById('singleOutletCode');
    const button = document.getElementById('btnSyncSingleOutlet');

    button?.addEventListener('click', runSingleOutletSync);
    document.getElementById('btnConfirmSingleOutlet')?.addEventListener('click', confirmSingleOutletSync);
    document.getElementById('btnCancelSingleOutlet')?.addEventListener('click', closeConfirmation);
    document.getElementById('singleOutletConfirmModal')?.addEventListener('click', event => {
        if (event.target === event.currentTarget) closeConfirmation();
    });
    input?.addEventListener('keydown', event => {
        if (event.key === 'Enter') runSingleOutletSync();
    });
    document.addEventListener('keydown', event => {
        if (event.key === 'Escape' && pendingOutletCode) closeConfirmation();
    });
}

function runSingleOutletSync() {
    const input = document.getElementById('singleOutletCode');
    const resultEl = document.getElementById('singleOutletResult');
    const code = String(input?.value || '').trim().toUpperCase();

    setResult(resultEl, '', '');
    if (!code) {
        setResult(resultEl, 'Enter an outlet code.', 'error');
        input?.focus();
        return;
    }
    if (!OUTLET_CODE_PATTERN.test(code)) {
        setResult(resultEl, 'Use only letters, numbers, hyphens, or underscores.', 'error');
        input?.focus();
        return;
    }

    openConfirmation(code);
}

function openConfirmation(code) {
    const modal = document.getElementById('singleOutletConfirmModal');
    const message = document.getElementById('singleOutletConfirmMessage');
    if (!modal || !message) return;

    pendingOutletCode = code;
    previouslyFocusedElement = document.activeElement;
    message.textContent = `Sync ${code} now? This will write Product, ProductPrice, and ProductBarCode data to the outlet database.`;
    modal.hidden = false;
    document.getElementById('btnConfirmSingleOutlet')?.focus();
}

function closeConfirmation() {
    const modal = document.getElementById('singleOutletConfirmModal');
    if (modal) modal.hidden = true;
    pendingOutletCode = null;
    previouslyFocusedElement?.focus();
    previouslyFocusedElement = null;
}

function confirmSingleOutletSync() {
    const code = pendingOutletCode;
    closeConfirmation();
    if (code) executeSingleOutletSync(code);
}

async function executeSingleOutletSync(code) {
    const input = document.getElementById('singleOutletCode');
    const button = document.getElementById('btnSyncSingleOutlet');
    const resultEl = document.getElementById('singleOutletResult');

    if (input) input.value = code;
    if (button) {
        button.disabled = true;
        button.textContent = 'Syncing...';
    }
    setResult(resultEl, `Synchronizing ${code}. Keep this page open...`, '');

    try {
        const result = await syncOutlet(code);
        if (result.status === 'Success') {
            const duration = Number.isFinite(result.duration_seconds)
                ? ` in ${result.duration_seconds.toFixed(1)} seconds`
                : '';
            setResult(resultEl, `${code} synchronized successfully${duration}.`, 'success');
            showToast(`${code} sync completed`, 'success');
        } else {
            const reason = result.remarks || result.error || 'Synchronization failed';
            setResult(resultEl, `${code} failed: ${reason}`, 'error');
            showToast(`${code} sync failed`, 'error');
        }
        await refreshOutlets();
    } catch (error) {
        setResult(resultEl, `${code} failed: ${error.message || 'Request failed'}`, 'error');
        showToast(error.message || `Failed to sync ${code}`, 'error');
    } finally {
        if (button) {
            button.disabled = false;
            button.textContent = 'Sync Single Outlet';
        }
    }
}

function setResult(element, message, state) {
    if (!element) return;
    element.textContent = message;
    element.classList.remove('success', 'error');
    if (state) element.classList.add(state);
}
