/**
 * schedule.js — Schedule configuration panel (visual + cron modes).
 *
 * Exports: init()
 */

import { applySchedule, getSettings, pauseSchedule, resumeSchedule } from '../api.js';
import { showToast, formatTime } from '../utils.js';

let pendingScheduleAction = null;
let previouslyFocusedElement = null;
const DAYWISE_DEFAULTS = [
    ['sun', true, 120, '7:00', '0:30'],
    ['mon', true, 120, '7:00', '0:30'],
    ['tue', true, 120, '7:00', '0:30'],
    ['wed', true, 120, '7:00', '0:30'],
    ['thu', true, 30, '9:00', '18:00'],
    ['fri', false, 120, '7:00', '0:30'],
    ['sat', false, 120, '7:00', '0:30'],
];

export function init() {
    renderDaywiseRules();
    // Tab switching
    const tabs = document.querySelectorAll('.schedule-tab');
    tabs.forEach(tab => {
        tab.addEventListener('click', () => {
            const mode = tab.dataset.mode || tab.textContent.trim().toLowerCase();
            tabs.forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            document.querySelectorAll('.schedule-form').forEach(f => f.classList.remove('active'));
            const form = document.getElementById(
                mode === 'cron' ? 'scheduleCron' : mode === 'daywise' ? 'scheduleDaywise' : 'scheduleVisual'
            );
            if (form) form.classList.add('active');
        });
    });

    // Day toggles
    document.querySelectorAll('.day-check').forEach(el => {
        el.addEventListener('click', () => el.classList.toggle('active'));
    });

    // Apply visual
    const btnVisual = document.getElementById('btnApplyVisual');
    if (btnVisual) btnVisual.addEventListener('click', requestVisualScheduleConfirmation);

    document.getElementById('btnApplyDaywise')?.addEventListener('click', requestDaywiseConfirmation);

    // Apply cron
    const btnCron = document.getElementById('btnApplyCron');
    if (btnCron) btnCron.addEventListener('click', applyCron);

    document.getElementById('btnPauseSchedule')?.addEventListener('click', () => openScheduleConfirmation('pause'));
    document.getElementById('btnResumeSchedule')?.addEventListener('click', () => openScheduleConfirmation('resume'));
    document.getElementById('btnConfirmScheduleAction')?.addEventListener('click', confirmScheduleAction);
    document.getElementById('btnCancelScheduleAction')?.addEventListener('click', closeScheduleConfirmation);
    document.getElementById('scheduleConfirmModal')?.addEventListener('click', event => {
        if (event.target === event.currentTarget) closeScheduleConfirmation();
    });
    document.addEventListener('keydown', event => {
        if (event.key === 'Escape' && pendingScheduleAction) closeScheduleConfirmation();
    });

    loadPersistedSchedule();
}

async function loadPersistedSchedule() {
    try {
        const data = await getSettings();
        const schedule = data?.schedule;
        if (!schedule) return;

        if (schedule.mode === 'cron') {
            activateScheduleMode('cron');
            const cronInput = document.getElementById('cronExpression');
            if (cronInput) cronInput.value = schedule.cron_expression || '';
            return;
        }

        if (schedule.mode === 'daywise') {
            activateScheduleMode('daywise');
            renderDaywiseRules(schedule.rules || []);
            return;
        }

        activateScheduleMode('visual');
        setSelectValue('scheduleInterval', schedule.interval_minutes);
        setSelectValue(
            'activeHoursStart',
            scheduleTimeValue(schedule.active_hours_start, schedule.active_minutes_start)
        );
        setSelectValue(
            'activeHoursEnd',
            scheduleTimeValue(schedule.active_hours_end, schedule.active_minutes_end)
        );

        const activeDays = new Set((schedule.active_days || []).map(day => String(day).toLowerCase()));
        document.querySelectorAll('.day-check').forEach(button => {
            button.classList.toggle('active', activeDays.has(String(button.dataset.day).toLowerCase()));
        });
    } catch {
        // The status polling will continue; leave the HTML defaults available.
    }
}

function activateScheduleMode(mode) {
    document.querySelectorAll('.schedule-tab').forEach(tab => {
        tab.classList.toggle('active', tab.dataset.mode === mode);
    });
    document.querySelectorAll('.schedule-form').forEach(form => form.classList.remove('active'));
    document.getElementById(
        mode === 'cron' ? 'scheduleCron' : mode === 'daywise' ? 'scheduleDaywise' : 'scheduleVisual'
    )?.classList.add('active');
}

function timeOptions(selected) {
    const options = [];
    for (let hour = 0; hour < 24; hour += 1) {
        for (const minute of [0, 30]) {
            const value = `${hour}:${String(minute).padStart(2, '0')}`;
            const displayHour = hour % 12 || 12;
            const label = `${displayHour}:${String(minute).padStart(2, '0')} ${hour < 12 ? 'AM' : 'PM'}`;
            options.push(`<option value="${value}"${value === selected ? ' selected' : ''}>${label}</option>`);
        }
    }
    return options.join('');
}

function renderDaywiseRules(rules = null) {
    const container = document.getElementById('daywiseRules');
    if (!container) return;
    const source = rules?.length
        ? DAYWISE_DEFAULTS.map(defaultRule => {
            const saved = rules.find(rule => rule.day === defaultRule[0]);
            return saved
                ? [saved.day, saved.enabled !== false, saved.interval_minutes,
                    scheduleTimeValue(saved.active_hours_start, saved.active_minutes_start),
                    scheduleTimeValue(saved.active_hours_end, saved.active_minutes_end)]
                : [defaultRule[0], false, defaultRule[2], defaultRule[3], defaultRule[4]];
        })
        : DAYWISE_DEFAULTS;
    container.innerHTML = source.map(([day, enabled, interval, start, end]) => `
        <div class="daywise-rule${enabled ? '' : ' disabled'}" data-day="${day}">
            <label><input class="daywise-enabled" type="checkbox"${enabled ? ' checked' : ''}> ${day.charAt(0).toUpperCase() + day.slice(1)}</label>
            <select class="daywise-interval" aria-label="${day} interval">
                ${[5,10,15,20,25,30,35,40,45,50,55,60,75,120].map(value =>
                    `<option value="${value}"${Number(interval) === value ? ' selected' : ''}>Every ${value < 60 ? `${value} min` : value === 60 ? '1 hour' : value === 120 ? '2 hours' : '1.25 hours'}</option>`
                ).join('')}
            </select>
            <select class="daywise-start" aria-label="${day} active from">${timeOptions(start)}</select>
            <select class="daywise-end" aria-label="${day} active until">${timeOptions(end)}</select>
        </div>`).join('');
    container.querySelectorAll('.daywise-enabled').forEach(checkbox => {
        checkbox.addEventListener('change', () => {
            checkbox.closest('.daywise-rule').classList.toggle('disabled', !checkbox.checked);
        });
    });
}

function getDaywiseRules() {
    return Array.from(document.querySelectorAll('.daywise-rule')).map(row => {
        const start = parseScheduleTime(row.querySelector('.daywise-start').value);
        const end = parseScheduleTime(row.querySelector('.daywise-end').value);
        return {
            day: row.dataset.day,
            enabled: row.querySelector('.daywise-enabled').checked,
            interval_minutes: Number(row.querySelector('.daywise-interval').value),
            active_hours_start: start.hour,
            active_minutes_start: start.minute,
            active_hours_end: end.hour,
            active_minutes_end: end.minute,
        };
    });
}

function setSelectValue(id, value) {
    const select = document.getElementById(id);
    const stringValue = String(value);
    if (select && Array.from(select.options).some(option => option.value === stringValue)) {
        select.value = stringValue;
    }
}

function scheduleTimeValue(hour = 0, minute = 0) {
    return `${Number(hour)}:${String(Number(minute || 0)).padStart(2, '0')}`;
}

function openScheduleConfirmation(action) {
    const modal = document.getElementById('scheduleConfirmModal');
    const title = document.getElementById('scheduleConfirmTitle');
    const message = document.getElementById('scheduleConfirmMessage');
    const confirmButton = document.getElementById('btnConfirmScheduleAction');
    if (!modal || !title || !message || !confirmButton) return;

    pendingScheduleAction = action;
    previouslyFocusedElement = document.activeElement;
    if (action === 'pause') {
        title.textContent = 'Pause Schedule';
        message.textContent = 'Pause automatic synchronization? No new scheduled sync cycles will start until you resume the schedule.';
        confirmButton.textContent = 'Pause Schedule';
    } else if (action === 'resume') {
        title.textContent = 'Resume Schedule';
        message.textContent = 'Resume automatic synchronization using the currently configured interval, active time, and active days?';
        confirmButton.textContent = 'Resume Schedule';
    } else {
        if (action === 'apply-daywise') {
            const enabled = getDaywiseRules().filter(rule => rule.enabled);
            title.textContent = 'Apply Day-wise Schedule';
            message.textContent = `Apply ${enabled.length} active day-specific schedule rule(s)? Disabled days will not run.`;
            confirmButton.textContent = 'Apply Day-wise Schedule';
            modal.hidden = false;
            confirmButton.focus();
            return;
        }
        const intervalSelect = document.getElementById('scheduleInterval');
        const startSelect = document.getElementById('activeHoursStart');
        const endSelect = document.getElementById('activeHoursEnd');
        const intervalLabel = intervalSelect?.selectedOptions[0]?.textContent || '';
        const startLabel = startSelect?.selectedOptions[0]?.textContent || '';
        const endLabel = endSelect?.selectedOptions[0]?.textContent || '';
        const days = getSelectedDays().map(day => day.charAt(0).toUpperCase() + day.slice(1)).join(', ');
        title.textContent = 'Apply Schedule';
        message.textContent = `Apply this schedule: every ${intervalLabel}, active from ${startLabel} until ${endLabel}, on ${days}?`;
        confirmButton.textContent = 'Apply Schedule';
    }
    modal.hidden = false;
    confirmButton.focus();
}

function closeScheduleConfirmation() {
    const modal = document.getElementById('scheduleConfirmModal');
    if (modal) modal.hidden = true;
    pendingScheduleAction = null;
    previouslyFocusedElement?.focus();
    previouslyFocusedElement = null;
}

function confirmScheduleAction() {
    const action = pendingScheduleAction;
    closeScheduleConfirmation();
    if (action === 'pause') pause();
    if (action === 'resume') resume();
    if (action === 'apply-visual') applyVisual();
    if (action === 'apply-daywise') applyDaywise();
}

async function pause() {
    try {
        const data = await pauseSchedule();
        document.getElementById('btnPauseSchedule').hidden = true;
        document.getElementById('btnResumeSchedule').hidden = false;
        showToast(data.message, 'info');
    } catch (error) {
        showToast(error.message || 'Failed to pause schedule', 'error');
    }
}

async function resume() {
    try {
        const data = await resumeSchedule();
        document.getElementById('btnPauseSchedule').hidden = false;
        document.getElementById('btnResumeSchedule').hidden = true;
        showToast(data.message, 'success');
    } catch (error) {
        showToast(error.message || 'Failed to resume schedule', 'error');
    }
}

function getSelectedDays() {
    return Array.from(document.querySelectorAll('.day-check.active'))
        .map(el => el.dataset.day);
}

function parseScheduleTime(value) {
    const [hour, minute] = value.split(':').map(Number);
    return { hour, minute };
}

function requestVisualScheduleConfirmation() {
    if (!getSelectedDays().length) {
        showToast('Select at least one day', 'error');
        return;
    }
    openScheduleConfirmation('apply-visual');
}

function requestDaywiseConfirmation() {
    if (!getDaywiseRules().some(rule => rule.enabled)) {
        showToast('Enable at least one day', 'error');
        return;
    }
    openScheduleConfirmation('apply-daywise');
}

async function applyDaywise() {
    const statusEl = document.getElementById('daywiseStatus');
    if (statusEl) statusEl.textContent = 'Applying...';
    try {
        const data = await applySchedule({ mode: 'daywise', rules: getDaywiseRules() });
        showToast(data.message, 'success');
        if (statusEl) statusEl.textContent = data.next_run ? `Next run: ${formatTime(data.next_run)}` : '';
    } catch (error) {
        showToast(error.message || 'Failed to apply day-wise schedule', 'error');
        if (statusEl) statusEl.textContent = '';
    }
}

async function applyVisual() {
    const interval = parseInt(document.getElementById('scheduleInterval').value);
    const start = parseScheduleTime(document.getElementById('activeHoursStart').value);
    const end = parseScheduleTime(document.getElementById('activeHoursEnd').value);
    const days = getSelectedDays();

    if (!days.length) {
        showToast('Select at least one day', 'error');
        return;
    }

    const statusEl = document.getElementById('visualStatus');
    if (statusEl) statusEl.textContent = 'Applying...';

    try {
        const data = await applySchedule({
            mode: 'visual',
            interval_minutes: interval,
            active_hours_start: start.hour,
            active_minutes_start: start.minute,
            active_hours_end: end.hour,
            active_minutes_end: end.minute,
            active_days: days,
        });
        showToast(data.message, 'success');
        if (statusEl) statusEl.textContent = data.next_run ? `Next run: ${formatTime(data.next_run)}` : '';
    } catch (e) {
        showToast(e.message || 'Failed to apply schedule', 'error');
        if (statusEl) statusEl.textContent = '';
    }
}

async function applyCron() {
    const cron = document.getElementById('cronExpression').value.trim();
    if (!cron) {
        showToast('Enter a cron expression', 'error');
        return;
    }

    const statusEl = document.getElementById('cronStatus');
    if (statusEl) statusEl.textContent = 'Applying...';

    try {
        const data = await applySchedule({
            mode: 'cron',
            cron_expression: cron,
        });
        showToast(data.message, 'success');
        if (statusEl) statusEl.textContent = data.next_run ? `Next run: ${formatTime(data.next_run)}` : '';
    } catch (e) {
        showToast(e.message || 'Invalid cron expression', 'error');
        if (statusEl) statusEl.textContent = '';
    }
}
