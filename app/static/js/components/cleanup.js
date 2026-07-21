import { triggerCleanup } from '../api.js';
import { showToast } from '../utils.js';

export function init() {
    document.querySelector('.cleanup-header')?.addEventListener('click', toggleCleanup);
    document.getElementById('btnTriggerCleanup')?.addEventListener('click', triggerNow);
}

function toggleCleanup(event) {
    document.getElementById('cleanupBody')?.classList.toggle('hidden');
    document.getElementById('cleanupToggleIcon')?.classList.toggle('collapsed');
}

async function triggerNow() {
    const button = document.getElementById('btnTriggerCleanup');
    const result = document.getElementById('cleanupResult');
    const retention = Number.parseInt(document.getElementById('cleanupRetention').value, 10);
    const batchSize = Number.parseInt(document.getElementById('cleanupBatchSize').value, 10);
    if (!button || !result) return;

    button.disabled = true;
    button.textContent = 'Cleaning up...';
    result.className = 'cleanup-result show running';
    result.textContent = 'Initiating price change log cleanup...';

    try {
        const data = await triggerCleanup({
            retention_days: retention,
            batch_size: batchSize,
        });
        const deleted = Number(data.deleted).toLocaleString();
        result.className = 'cleanup-result show success';
        const heading = document.createElement('strong');
        heading.textContent = 'Cleanup complete';
        const details = document.createElement('div');
        details.textContent = `Deleted ${deleted} records older than ${data.retention_days} days (batch size: ${data.batch_size})`;
        result.replaceChildren(heading, details);
        showToast(`Cleaned up ${deleted} records`, 'success');
    } catch (error) {
        result.className = 'cleanup-result show error';
        result.textContent = error.message || 'Cleanup failed';
        showToast(error.message || 'Cleanup failed', 'error');
    } finally {
        button.disabled = false;
        button.textContent = 'Trigger Cleanup';
    }
}
