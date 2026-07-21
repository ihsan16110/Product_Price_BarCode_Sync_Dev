jest.mock('../../app/static/js/api.js', () => ({
    getOutlets: jest.fn(),
    syncOutlet: jest.fn(),
    getPriceChanges: jest.fn(),
}));

import { getOutlets } from '../../app/static/js/api.js';
import { init as initOutlets, refresh as refreshOutlets } from '../../app/static/js/components/outlets.js';
import { render as renderPriceChanges } from '../../app/static/js/components/price-changes.js';

describe('DOM-safe component rendering', () => {
    beforeEach(() => {
        document.body.replaceChildren();
        jest.clearAllMocks();
    });

    test('outlet values are rendered as text and retry uses an event listener', async () => {
        document.body.innerHTML = `
            <input id="outletSearch"><button id="outletSearchClear"></button>
            <span id="resultsBadge"></span><span id="searchCount"></span>
            <table><tbody id="outletsTableBody"></tbody></table>`;
        getOutlets.mockResolvedValue([{
            outlet_code: `F001');alert(1);//<script>bad()</script>`,
            ip: '<img src=x onerror=alert(1)>',
            status: 'N',
            remarks: '<svg onload=alert(1)>',
            timestamp: '2026-01-01T00:00:00',
            duration_seconds: 1,
        }]);

        initOutlets();
        await refreshOutlets();

        expect(document.querySelector('script')).toBeNull();
        expect(document.querySelector('img')).toBeNull();
        expect(document.querySelector('svg')).toBeNull();
        expect(document.getElementById('outletsTableBody').textContent).toContain('<script>bad()</script>');
        expect(document.querySelector('#outletsTableBody button').getAttribute('onclick')).toBeNull();
    });

    test('price-change values cannot create executable elements', () => {
        document.body.innerHTML = `
            <span id="priceChangeBadge"></span>
            <table><tbody id="priceChangesBody"></tbody></table>`;
        renderPriceChanges({
            count: 1,
            changes: [{
                product_code: '<script>bad()</script>',
                depot_code: '<img src=x>',
                previous_unit_price: 1,
                current_unit_price: 2,
                changed_by: '<svg onload=bad()>',
                outlet_code: 'F001',
            }],
        });

        expect(document.querySelector('script')).toBeNull();
        expect(document.querySelector('img')).toBeNull();
        expect(document.querySelector('svg')).toBeNull();
        expect(document.getElementById('priceChangesBody').textContent).toContain('<script>bad()</script>');
    });
});
