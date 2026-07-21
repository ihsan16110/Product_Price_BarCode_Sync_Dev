jest.mock('../../app/static/js/api.js', () => ({
    applySchedule: jest.fn(),
    getSettings: jest.fn(),
    pauseSchedule: jest.fn(),
    resumeSchedule: jest.fn(),
}));

jest.mock('../../app/static/js/utils.js', () => ({
    showToast: jest.fn(),
    formatTime: jest.fn(value => value),
}));

import { applySchedule, getSettings, pauseSchedule, resumeSchedule } from '../../app/static/js/api.js';
import { init } from '../../app/static/js/components/schedule.js';

describe('visual schedule controls', () => {
    beforeEach(() => {
        jest.clearAllMocks();
        applySchedule.mockResolvedValue({ message: 'Applied', next_run: null });
        getSettings.mockResolvedValue({
            schedule: {
                mode: 'visual', interval_minutes: 35,
                active_hours_start: 12, active_minutes_start: 30,
                active_hours_end: 21, active_minutes_end: 30,
                active_days: ['sat'],
            },
        });
        pauseSchedule.mockResolvedValue({ message: 'Paused', enabled: false });
        resumeSchedule.mockResolvedValue({ message: 'Resumed', enabled: true });
        document.body.innerHTML = `
            <button class="schedule-tab active" data-mode="visual"></button>
            <div class="schedule-form active" id="scheduleVisual"></div>
            <select id="scheduleInterval"><option value="30">30 min</option><option value="35" selected>35 min</option><option value="45">45 min</option></select>
            <select id="activeHoursStart"><option value="0:00">12:00 AM</option><option value="12:30" selected>12:30 PM</option></select>
            <select id="activeHoursEnd"><option value="21:30" selected>9:30 PM</option><option value="23:00">11:00 PM</option></select>
            <button class="day-check active" data-day="sat" type="button">Sat</button>
            <button id="btnApplyVisual" type="button">Apply</button>
            <span id="visualStatus"></span>
            <button id="btnPauseSchedule" type="button">Pause Schedule</button>
            <button id="btnResumeSchedule" type="button" hidden>Resume Schedule</button>
            <div id="scheduleConfirmModal" hidden>
                <h2 id="scheduleConfirmTitle"></h2>
                <p id="scheduleConfirmMessage"></p>
                <button id="btnConfirmScheduleAction" type="button">Confirm</button>
                <button id="btnCancelScheduleAction" type="button">Cancel</button>
            </div>`;
    });

    test('sends selected half-hour boundaries to the API', async () => {
        init();
        await Promise.resolve();
        await Promise.resolve();
        document.getElementById('btnApplyVisual').click();

        expect(document.getElementById('scheduleConfirmModal').hidden).toBe(false);
        expect(document.getElementById('scheduleConfirmTitle').textContent).toBe('Apply Schedule');
        expect(document.getElementById('scheduleConfirmMessage').textContent).toContain('every 35 min');
        expect(document.getElementById('scheduleConfirmMessage').textContent).toContain('12:30 PM');
        expect(document.getElementById('scheduleConfirmMessage').textContent).toContain('Sat');
        expect(applySchedule).not.toHaveBeenCalled();

        document.getElementById('btnConfirmScheduleAction').click();
        await Promise.resolve();
        await Promise.resolve();

        expect(applySchedule).toHaveBeenCalledWith({
            mode: 'visual',
            interval_minutes: 35,
            active_hours_start: 12,
            active_minutes_start: 30,
            active_hours_end: 21,
            active_minutes_end: 30,
            active_days: ['sat'],
        });
    });

    test('restores the persisted visual schedule after page refresh', async () => {
        getSettings.mockResolvedValue({
            schedule: {
                mode: 'visual', interval_minutes: 45,
                active_hours_start: 0, active_minutes_start: 0,
                active_hours_end: 23, active_minutes_end: 0,
                active_days: ['sat'],
            },
        });

        init();
        await Promise.resolve();
        await Promise.resolve();

        expect(document.getElementById('scheduleInterval').value).toBe('45');
        expect(document.getElementById('activeHoursStart').value).toBe('0:00');
        expect(document.getElementById('activeHoursEnd').value).toBe('23:00');
        expect(document.querySelector('[data-day="sat"]').classList).toContain('active');
    });

    test('submits independent day-wise rules and disabled days', async () => {
        document.body.innerHTML += `
            <button class="schedule-tab" data-mode="daywise"></button>
            <div class="schedule-form" id="scheduleDaywise">
                <div id="daywiseRules"></div>
                <button id="btnApplyDaywise" type="button">Apply Day-wise</button>
                <span id="daywiseStatus"></span>
            </div>`;
        getSettings.mockResolvedValue({ schedule: { mode: 'daywise', rules: [
            { day: 'sun', enabled: true, interval_minutes: 120,
                active_hours_start: 7, active_minutes_start: 0,
                active_hours_end: 0, active_minutes_end: 30 },
            { day: 'thu', enabled: true, interval_minutes: 30,
                active_hours_start: 9, active_minutes_start: 0,
                active_hours_end: 18, active_minutes_end: 0 },
        ] } });

        init();
        await Promise.resolve();
        await Promise.resolve();
        document.getElementById('btnApplyDaywise').click();
        document.getElementById('btnConfirmScheduleAction').click();
        await Promise.resolve();
        await Promise.resolve();

        const payload = applySchedule.mock.calls[0][0];
        expect(payload.mode).toBe('daywise');
        expect(payload.rules.find(rule => rule.day === 'sun')).toMatchObject({
            enabled: true, interval_minutes: 120,
            active_hours_start: 7, active_hours_end: 0, active_minutes_end: 30,
        });
        expect(payload.rules.find(rule => rule.day === 'thu')).toMatchObject({
            enabled: true, interval_minutes: 30,
            active_hours_start: 9, active_hours_end: 18,
        });
        expect(payload.rules.find(rule => rule.day === 'fri').enabled).toBe(false);
    });

    test('cancel does not apply the selected visual schedule', async () => {
        init();
        document.getElementById('btnApplyVisual').click();
        document.getElementById('btnCancelScheduleAction').click();
        await Promise.resolve();

        expect(document.getElementById('scheduleConfirmModal').hidden).toBe(true);
        expect(applySchedule).not.toHaveBeenCalled();
    });

    test('requires confirmation before pausing the schedule', async () => {
        init();
        document.getElementById('btnPauseSchedule').click();

        expect(document.getElementById('scheduleConfirmModal').hidden).toBe(false);
        expect(document.getElementById('scheduleConfirmTitle').textContent).toBe('Pause Schedule');
        expect(pauseSchedule).not.toHaveBeenCalled();

        document.getElementById('btnConfirmScheduleAction').click();
        await Promise.resolve();
        await Promise.resolve();

        expect(pauseSchedule).toHaveBeenCalledTimes(1);
        expect(document.getElementById('btnPauseSchedule').hidden).toBe(true);
        expect(document.getElementById('btnResumeSchedule').hidden).toBe(false);
    });

    test('cancel leaves the schedule unchanged', async () => {
        init();
        document.getElementById('btnPauseSchedule').click();
        document.getElementById('btnCancelScheduleAction').click();
        await Promise.resolve();

        expect(document.getElementById('scheduleConfirmModal').hidden).toBe(true);
        expect(pauseSchedule).not.toHaveBeenCalled();
    });

    test('requires confirmation before resuming the schedule', async () => {
        init();
        document.getElementById('btnResumeSchedule').click();

        expect(document.getElementById('scheduleConfirmTitle').textContent).toBe('Resume Schedule');
        expect(resumeSchedule).not.toHaveBeenCalled();

        document.getElementById('btnConfirmScheduleAction').click();
        await Promise.resolve();
        await Promise.resolve();

        expect(resumeSchedule).toHaveBeenCalledTimes(1);
        expect(document.getElementById('btnPauseSchedule').hidden).toBe(false);
        expect(document.getElementById('btnResumeSchedule').hidden).toBe(true);
    });
});
