jest.mock('../../app/static/js/api.js', () => ({
    syncOutlet: jest.fn(),
}));

jest.mock('../../app/static/js/utils.js', () => ({
    showToast: jest.fn(),
}));

jest.mock('../../app/static/js/components/outlets.js', () => ({
    refresh: jest.fn(),
}));

import { syncOutlet } from '../../app/static/js/api.js';
import { refresh as refreshOutlets } from '../../app/static/js/components/outlets.js';
import { init } from '../../app/static/js/components/single-outlet.js';

describe('single outlet sync card', () => {
    beforeEach(() => {
        jest.clearAllMocks();
        document.body.innerHTML = `
            <input id="singleOutletCode">
            <button id="btnSyncSingleOutlet" type="button">Sync Single Outlet</button>
            <div id="singleOutletResult"></div>
            <div id="singleOutletConfirmModal" hidden>
                <p id="singleOutletConfirmMessage"></p>
                <button id="btnConfirmSingleOutlet" type="button">Confirm</button>
                <button id="btnCancelSingleOutlet" type="button">Cancel</button>
            </div>`;
    });

    test('validates the outlet code before making a request', async () => {
        init();
        document.getElementById('singleOutletCode').value = 'F 001';
        document.getElementById('btnSyncSingleOutlet').click();
        await Promise.resolve();

        expect(syncOutlet).not.toHaveBeenCalled();
        expect(document.getElementById('singleOutletResult').textContent).toContain('letters, numbers');
    });

    test('confirms and synchronizes one normalized outlet code', async () => {
        syncOutlet.mockResolvedValue({ status: 'Success', duration_seconds: 1.25 });
        refreshOutlets.mockResolvedValue();
        init();
        document.getElementById('singleOutletCode').value = 'f001';
        document.getElementById('btnSyncSingleOutlet').click();
        expect(document.getElementById('singleOutletConfirmModal').hidden).toBe(false);
        expect(document.getElementById('singleOutletConfirmMessage').textContent).toContain('F001');
        expect(syncOutlet).not.toHaveBeenCalled();

        document.getElementById('btnConfirmSingleOutlet').click();
        await Promise.resolve();
        await Promise.resolve();
        await Promise.resolve();

        expect(document.getElementById('singleOutletConfirmModal').hidden).toBe(true);
        expect(syncOutlet).toHaveBeenCalledWith('F001');
        expect(refreshOutlets).toHaveBeenCalled();
        expect(document.getElementById('singleOutletResult').textContent).toContain('synchronized successfully');
        expect(document.getElementById('singleOutletResult').classList).toContain('success');
    });

    test('cancel closes the modal without synchronizing', async () => {
        init();
        document.getElementById('singleOutletCode').value = 'F002';
        document.getElementById('btnSyncSingleOutlet').click();
        document.getElementById('btnCancelSingleOutlet').click();
        await Promise.resolve();

        expect(document.getElementById('singleOutletConfirmModal').hidden).toBe(true);
        expect(syncOutlet).not.toHaveBeenCalled();
    });
});
