import { getRetries, processRetries, clearRetries } from '../api.js';
import { formatTime, truncate, showToast } from '../utils.js';

let pendingRetryAction = null;
let previouslyFocusedElement = null;

export function init() {
    document.getElementById('btnRetryAll')?.addEventListener('click', () => openConfirmation('retry'));
    document.getElementById('btnClearRetries')?.addEventListener('click', () => openConfirmation('clear'));
    document.getElementById('btnConfirmRetryAction')?.addEventListener('click', confirmRetryAction);
    document.getElementById('btnCancelRetryAction')?.addEventListener('click', closeConfirmation);
    document.getElementById('retryConfirmModal')?.addEventListener('click', event => {
        if (event.target === event.currentTarget) closeConfirmation();
    });
    document.addEventListener('keydown', event => {
        if (event.key === 'Escape' && pendingRetryAction) closeConfirmation();
    });
}

function openConfirmation(action) {
    const modal = document.getElementById('retryConfirmModal');
    const title = document.getElementById('retryConfirmTitle');
    const message = document.getElementById('retryConfirmMessage');
    const confirmButton = document.getElementById('btnConfirmRetryAction');
    if (!modal || !title || !message || !confirmButton) return;

    pendingRetryAction = action;
    previouslyFocusedElement = document.activeElement;
    if (action === 'retry') {
        title.textContent = 'Retry Failed Outlets';
        message.textContent = 'Process all currently due failed-outlet retries now? This may write synchronized data to multiple outlet databases.';
        confirmButton.textContent = 'Retry Failed';
    } else {
        title.textContent = 'Clear Retry Queue';
        message.textContent = 'Remove every entry from the retry queue? Cleared outlets will not retry automatically unless they are added again.';
        confirmButton.textContent = 'Clear Retry Queue';
    }
    modal.hidden = false;
    confirmButton.focus();
}

function closeConfirmation() {
    const modal = document.getElementById('retryConfirmModal');
    if (modal) modal.hidden = true;
    pendingRetryAction = null;
    previouslyFocusedElement?.focus();
    previouslyFocusedElement = null;
}

function confirmRetryAction() {
    const action = pendingRetryAction;
    closeConfirmation();
    if (action === 'retry') retryAll();
    if (action === 'clear') clearAll();
}

export async function refresh() {
    try {
        render(await getRetries());
    } catch {
        // Polling retries automatically.
    }
}

function cell(row, value) {
    const node = document.createElement('td');
    node.textContent = value == null ? '' : String(value);
    row.appendChild(node);
    return node;
}

function render(data) {
    const tbody = document.getElementById('retryTableBody');
    if (!tbody) return;
    const badge = document.getElementById('retryBadge');
    if (badge) badge.textContent = `${data.size} entries`;
    tbody.replaceChildren();

    if (!data.entries.length) {
        const row = document.createElement('tr');
        const node = cell(row, 'Queue empty');
        node.colSpan = 5;
        node.style.textAlign = 'center';
        tbody.appendChild(row);
        return;
    }

    for (const entry of data.entries) {
        const row = document.createElement('tr');
        cell(row, entry.outlet_code);
        cell(row, entry.server);
        cell(row, `${entry.attempt}/${entry.max_attempts}${entry.permanently_failed ? ' EXHAUSTED' : ''}`);
        cell(row, formatTime(entry.next_retry_at));
        const error = cell(row, truncate(entry.last_error, 80));
        error.title = String(entry.last_error || '');
        tbody.appendChild(row);
    }
}

async function retryAll() {
    try {
        const data = await processRetries();
        showToast(data.message, 'info');
    } catch (error) {
        showToast(error.message || 'Failed to process retries', 'error');
    }
}

async function clearAll() {
    try {
        const data = await clearRetries();
        showToast(data.message, 'info');
        await refresh();
    } catch (error) {
        showToast(error.message || 'Failed to clear retries', 'error');
    }
}
