import { getOutlets, syncOutlet } from '../api.js';
import { formatDuration, formatTime, truncate, showToast } from '../utils.js';

let allOutlets = [];

export function init() {
    const input = document.getElementById('outletSearch');
    const clear = document.getElementById('outletSearchClear');
    input?.addEventListener('input', render);
    clear?.addEventListener('click', () => {
        if (input) input.value = '';
        clear.classList.remove('visible');
        render();
    });
}

export async function refresh() {
    try {
        allOutlets = await getOutlets();
        render();
    } catch {
        // Polling retries automatically.
    }
}

function appendCell(row, value, className = '') {
    const cell = document.createElement('td');
    cell.textContent = value == null ? '' : String(value);
    if (className) cell.className = className;
    row.appendChild(cell);
    return cell;
}

function appendEmptyState(tbody, message, hint = '') {
    const row = document.createElement('tr');
    const cell = appendCell(row, '');
    cell.colSpan = 7;
    cell.className = 'no-results';
    const text = document.createElement('div');
    text.className = 'no-results-text';
    text.textContent = message;
    cell.appendChild(text);
    if (hint) {
        const hintNode = document.createElement('div');
        hintNode.className = 'no-results-hint';
        hintNode.textContent = hint;
        cell.appendChild(hintNode);
    }
    tbody.appendChild(row);
}

export function render() {
    const tbody = document.getElementById('outletsTableBody');
    if (!tbody) return;
    const searchInput = document.getElementById('outletSearch');
    const rawSearch = searchInput?.value.trim() || '';
    const searchTerm = rawSearch.toLowerCase();
    document.getElementById('outletSearchClear')?.classList.toggle('visible', Boolean(searchTerm));

    const filtered = allOutlets
        .filter(outlet => !searchTerm ||
            String(outlet.outlet_code || '').toLowerCase().includes(searchTerm) ||
            String(outlet.ip || '').toLowerCase().includes(searchTerm))
        .sort((left, right) => {
            if (left.status !== right.status) return left.status === 'N' ? -1 : 1;
            return String(left.outlet_code).localeCompare(String(right.outlet_code));
        });

    const badge = document.getElementById('resultsBadge');
    if (badge) badge.textContent = `${allOutlets.length} outlets`;
    const count = document.getElementById('searchCount');
    if (count) count.textContent = searchTerm
        ? `${filtered.length} of ${allOutlets.length} ${filtered.length === 1 ? 'match' : 'matches'}`
        : '';

    tbody.replaceChildren();
    if (!filtered.length) {
        appendEmptyState(
            tbody,
            allOutlets.length ? `No outlets match "${rawSearch}"` : 'No data yet. Start a sync cycle.',
            allOutlets.length ? 'Try an outlet code or IP address.' : '',
        );
        return;
    }

    for (const outlet of filtered) {
        const row = document.createElement('tr');
        const codeCell = appendCell(row, outlet.outlet_code);
        const strong = document.createElement('strong');
        strong.textContent = codeCell.textContent;
        codeCell.replaceChildren(strong);
        appendCell(row, outlet.ip);
        const statusLabel = outlet.status === 'Success'
            ? 'Success'
            : outlet.status === 'Partial' ? 'Partial' : 'Failed';
        const statusClass = outlet.status === 'Success'
            ? 'status-success'
            : outlet.status === 'Partial' ? 'status-partial' : 'status-failed';
        const statusCell = appendCell(row, statusLabel);
        statusCell.className = statusClass;
        appendCell(row, formatDuration(outlet.duration_seconds));
        const remarks = appendCell(row, truncate(outlet.remarks, 60));
        remarks.title = String(outlet.remarks || '');
        appendCell(row, formatTime(outlet.timestamp));
        const action = appendCell(row, '');
        if (outlet.status === 'N') {
            const button = document.createElement('button');
            button.className = 'btn btn-sm btn-primary';
            button.type = 'button';
            button.textContent = 'Retry';
            button.addEventListener('click', () => retryOutlet(outlet.outlet_code));
            action.appendChild(button);
        }
        tbody.appendChild(row);
    }
}

async function retryOutlet(code) {
    try {
        await syncOutlet(code);
        showToast(`Retry started for ${code}`, 'success');
        await refresh();
    } catch (error) {
        showToast(error.message || `Failed to retry ${code}`, 'error');
    }
}
