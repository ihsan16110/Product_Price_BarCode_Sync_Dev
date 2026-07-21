jest.mock('../../app/static/js/api.js', () => ({
    getRetries: jest.fn(),
    processRetries: jest.fn(),
    clearRetries: jest.fn(),
}));

jest.mock('../../app/static/js/utils.js', () => ({
    formatTime: jest.fn(value => value),
    truncate: jest.fn(value => value),
    showToast: jest.fn(),
}));

import { processRetries, clearRetries, getRetries } from '../../app/static/js/api.js';
import { init } from '../../app/static/js/components/retries.js';

describe('retry action confirmations', () => {
    beforeEach(() => {
        jest.clearAllMocks();
        processRetries.mockResolvedValue({ message: 'Processing retries' });
        clearRetries.mockResolvedValue({ message: 'Queue cleared' });
        getRetries.mockResolvedValue({ size: 0, entries: [] });
        document.body.innerHTML = `
            <button id="btnRetryAll" type="button">Retry Failed</button>
            <button id="btnClearRetries" type="button">Clear Retry Queue</button>
            <div id="retryConfirmModal" hidden>
                <h2 id="retryConfirmTitle"></h2><p id="retryConfirmMessage"></p>
                <button id="btnConfirmRetryAction" type="button">Confirm</button>
                <button id="btnCancelRetryAction" type="button">Cancel</button>
            </div>
            <span id="retryBadge"></span><table><tbody id="retryTableBody"></tbody></table>`;
    });

    test('confirms before processing failed retries', async () => {
        init();
        document.getElementById('btnRetryAll').click();

        expect(document.getElementById('retryConfirmTitle').textContent).toBe('Retry Failed Outlets');
        expect(processRetries).not.toHaveBeenCalled();

        document.getElementById('btnConfirmRetryAction').click();
        await Promise.resolve();
        expect(processRetries).toHaveBeenCalledTimes(1);
    });

    test('confirms before clearing the queue', async () => {
        init();
        document.getElementById('btnClearRetries').click();

        expect(document.getElementById('retryConfirmTitle').textContent).toBe('Clear Retry Queue');
        expect(clearRetries).not.toHaveBeenCalled();

        document.getElementById('btnConfirmRetryAction').click();
        await Promise.resolve();
        await Promise.resolve();
        expect(clearRetries).toHaveBeenCalledTimes(1);
    });

    test('cancel makes no retry request', async () => {
        init();
        document.getElementById('btnRetryAll').click();
        document.getElementById('btnCancelRetryAction').click();
        await Promise.resolve();

        expect(document.getElementById('retryConfirmModal').hidden).toBe(true);
        expect(processRetries).not.toHaveBeenCalled();
        expect(clearRetries).not.toHaveBeenCalled();
    });
});
