import { getPriceChanges } from '../api.js';
import { formatTime } from '../utils.js';

export function init() {
    document.getElementById('btnRefreshPriceChanges')?.addEventListener('click', refresh);
    document.getElementById('priceOutletFilter')?.addEventListener('keydown', event => {
        if (event.key === 'Enter') refresh();
    });
}

export async function refresh() {
    try {
        const outletCode = document.getElementById('priceOutletFilter')?.value.trim() || '';
        const days = document.getElementById('priceDaysFilter')?.value || '7';
        render(await getPriceChanges({ days, limit: 100, outlet_code: outletCode || undefined }));
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

export function render(data) {
    const badge = document.getElementById('priceChangeBadge');
    if (badge) badge.textContent = `${data.count} changes`;
    const tbody = document.getElementById('priceChangesBody');
    if (!tbody) return;
    tbody.replaceChildren();

    if (!data.changes.length) {
        const row = document.createElement('tr');
        const node = cell(row, 'No price changes found for the selected filters.');
        node.colSpan = 11;
        node.style.textAlign = 'center';
        tbody.appendChild(row);
        return;
    }

    for (const change of data.changes) {
        const row = document.createElement('tr');
        cell(row, change.change_type || 'UPDATE');
        const runId = String(change.run_id || '-');
        const runCell = cell(row, runId === '-' ? runId : runId.slice(0, 8));
        runCell.title = runId;
        cell(row, change.product_code);
        cell(row, change.depot_code);
        cell(row, change.previous_unit_price == null ? '-' : `$${Number(change.previous_unit_price).toFixed(2)}`);
        cell(row, `$${Number(change.current_unit_price).toFixed(2)}`);
        cell(row, change.changed_by || '-');
        cell(row, formatTime(change.previous_modified_date));
        cell(row, formatTime(change.current_modified_date));
        cell(row, formatTime(change.change_occurrence_time));
        cell(row, change.outlet_code || '-');
        tbody.appendChild(row);
    }
}
