jest.mock('../../app/static/js/api.js', () => ({
    applySchedule: jest.fn().mockResolvedValue({ message: 'applied' }),
    clearRetries: jest.fn().mockResolvedValue({ message: 'cleared' }),
    getAuthHeaders: jest.fn().mockReturnValue({}),
    getHealth: jest.fn(),
    getLogs: jest.fn().mockResolvedValue(''),
    getOutlets: jest.fn().mockResolvedValue([]),
    getPriceChanges: jest.fn().mockResolvedValue({ count: 0, changes: [] }),
    getRetries: jest.fn().mockResolvedValue({ size: 0, entries: [] }),
    getSettings: jest.fn(),
    getStatus: jest.fn().mockResolvedValue({
        uptime_seconds: 1,
        retry_queue_size: 0,
        total_syncs_completed: 0,
        next_scheduled_run: null,
        schedule: { enabled: true, description: 'Every 30 min' },
        current_sync: {
            state: 'idle', trigger: '', total_outlets: 0, completed: 0,
            failed: 0, in_progress: 0, cancelled: 0,
        },
    }),
    hasApiKey: jest.fn().mockReturnValue(false),
    openLogStream: jest.fn(),
    pauseSchedule: jest.fn().mockResolvedValue({ message: 'paused' }),
    processRetries: jest.fn().mockResolvedValue({ message: 'processed' }),
    resumeSchedule: jest.fn().mockResolvedValue({ message: 'resumed' }),
    setApiKey: jest.fn(),
    startSync: jest.fn().mockResolvedValue({ message: 'started' }),
    stopSync: jest.fn().mockResolvedValue({ message: 'stopped', status: 'stopping' }),
    syncOutlet: jest.fn(),
    triggerCleanup: jest.fn().mockResolvedValue({
        deleted: 0, retention_days: 90, batch_size: 5000,
    }),
}));

import * as api from '../../app/static/js/api.js';

describe('dashboard module boot', () => {
    beforeEach(() => {
        jest.useFakeTimers();
        document.body.innerHTML = `
            <input id="apiKeyInput"><button id="btnConnectApi"></button><span id="authStatus"></span>
            <span id="uptime"></span><span id="syncState"></span><span id="syncTrigger"></span>
            <span id="totalOutlets"></span><span id="inProgress"></span><span id="successCount"></span>
            <span id="failedCount"></span><span id="retryCount"></span><span id="totalCycles"></span>
            <span id="lastRun"></span><span id="lastRunTrigger"></span><span id="nextRunCountdown"></span>
            <span id="nextRunTime"></span><section id="progressSection"></section>
            <div id="progressFill"></div><span id="progressText"></span><span id="scheduleDescription"></span>
            <button id="btnStartSync"></button><button id="btnStopSync"></button>
            <div id="fullSyncConfirmModal" hidden>
                <button id="btnConfirmFullSync"></button><button id="btnCancelFullSync"></button>
            </div>
            <div id="stopSyncConfirmModal" hidden>
                <button id="btnConfirmStopSync"></button><button id="btnCancelStopSync"></button>
            </div>
            <button id="btnPauseSchedule"></button><button id="btnResumeSchedule"></button>
            <button id="btnRetryAll"></button><button id="btnClearRetries"></button>
            <button id="btnApplyVisual"></button><button id="btnApplyCron"></button>
            <input id="outletSearch"><button id="outletSearchClear"></button>
            <span id="resultsBadge"></span><span id="searchCount"></span><tbody id="outletsTableBody"></tbody>
            <span id="retryBadge"></span><tbody id="retryTableBody"></tbody>
            <input id="priceOutletFilter"><select id="priceDaysFilter"><option value="7"></option></select>
            <button id="btnRefreshPriceChanges"></button><span id="priceChangeBadge"></span>
            <tbody id="priceChangesBody"></tbody>
            <button id="btnLoadLogs"></button><button id="btnReconnectLogs"></button><button id="btnClearLogs"></button>
            <input id="logDatePicker"><span id="liveLogStatus"></span><pre id="liveLogViewer"></pre><pre id="logViewer"></pre>
            <button class="cleanup-header"></button><div id="cleanupBody"></div><span id="cleanupToggleIcon"></span>
            <button id="btnTriggerCleanup"></button><input id="cleanupRetention" value="90">
            <input id="cleanupBatchSize" value="5000"><div id="cleanupResult"></div><div id="toast"></div>
        `;
    });

    afterEach(() => {
        jest.clearAllTimers();
        jest.useRealTimers();
        jest.resetModules();
    });

    test('initializes modules and binds primary controls without inline handlers', async () => {
        jest.isolateModules(() => {
            require('../../app/static/js/app.js');
        });
        document.dispatchEvent(new Event('DOMContentLoaded'));
        await Promise.resolve();
        await Promise.resolve();

        document.getElementById('btnStartSync').click();
        expect(document.getElementById('fullSyncConfirmModal').hidden).toBe(false);
        expect(api.startSync).not.toHaveBeenCalled();

        document.getElementById('btnConfirmFullSync').click();
        await Promise.resolve();

        expect(api.getStatus).toHaveBeenCalled();
        expect(api.getOutlets).toHaveBeenCalled();
        expect(api.startSync).toHaveBeenCalled();
        expect(document.getElementById('logViewer').textContent).toContain('API key');
    });

    test('cancelling manual full sync makes no start request', async () => {
        jest.isolateModules(() => {
            require('../../app/static/js/app.js');
        });
        document.dispatchEvent(new Event('DOMContentLoaded'));
        await Promise.resolve();

        document.getElementById('btnStartSync').click();
        document.getElementById('btnCancelFullSync').click();

        expect(document.getElementById('fullSyncConfirmModal').hidden).toBe(true);
        expect(api.startSync).not.toHaveBeenCalled();
    });

    test('requires confirmation before stopping the current sync', async () => {
        jest.isolateModules(() => {
            require('../../app/static/js/app.js');
        });
        document.dispatchEvent(new Event('DOMContentLoaded'));
        await Promise.resolve();

        const stopButton = document.getElementById('btnStopSync');
        stopButton.disabled = false;
        stopButton.click();
        expect(document.getElementById('stopSyncConfirmModal').hidden).toBe(false);
        expect(api.stopSync).not.toHaveBeenCalled();

        document.getElementById('btnConfirmStopSync').click();
        await Promise.resolve();

        expect(document.getElementById('stopSyncConfirmModal').hidden).toBe(true);
        expect(api.stopSync).toHaveBeenCalledTimes(1);
    });

    test('cancelling stop keeps the current sync running', async () => {
        jest.isolateModules(() => {
            require('../../app/static/js/app.js');
        });
        document.dispatchEvent(new Event('DOMContentLoaded'));
        await Promise.resolve();

        const stopButton = document.getElementById('btnStopSync');
        stopButton.disabled = false;
        stopButton.click();
        document.getElementById('btnCancelStopSync').click();

        expect(document.getElementById('stopSyncConfirmModal').hidden).toBe(true);
        expect(api.stopSync).not.toHaveBeenCalled();
    });
});
