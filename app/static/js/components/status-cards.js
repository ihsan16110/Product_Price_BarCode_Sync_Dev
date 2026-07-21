/**
 * status-cards.js — Status cards, uptime, progress bar, and sync state.
 *
 * Exports:
 *   init()   – one-time setup
 *   refresh() – fetch /status and update the DOM
 */

import { getStatus } from '../api.js';
import { formatTime, setNextRunTimestamp, showToast } from '../utils.js';

const EL = {
    uptime:          'uptime',
    syncState:       'syncState',
    syncTrigger:     'syncTrigger',
    totalOutlets:    'totalOutlets',
    inProgress:      'inProgress',
    successCount:    'successCount',
    failedCount:     'failedCount',
    retryCount:      'retryCount',
    totalCycles:     'totalCycles',
    lastRun:         'lastRun',
    lastRunTrigger:  'lastRunTrigger',
    progressSection: 'progressSection',
    progressFill:    'progressFill',
    progressText:    'progressText',
    btnStartSync:    'btnStartSync',
    btnStopSync:     'btnStopSync',
    scheduleDesc:    'scheduleDescription',
};

let state = 'idle'; // 'idle' | 'running' | 'stopping'

/** One-time setup (nothing to bind for this component). */
export function init() {}

/** Fetch status and update all card values. */
export async function refresh() {
    try {
        const data = await getStatus();
        render(data);
    } catch (e) {
        const el = document.getElementById(EL.uptime);
        if (el) el.textContent = 'Connection lost';
    }
}

function render(data) {
    const sync = data.current_sync;
    state = sync.state;

    // ── Uptime ──
    const hrs = Math.floor(data.uptime_seconds / 3600);
    const mins = Math.floor((data.uptime_seconds % 3600) / 60);
    setText(EL.uptime, `Uptime: ${hrs}h ${mins}m`);

    // ── Sync state ──
    const stateEl = setText(EL.syncState, sync.state === 'stopping' ? 'STOPPING' : sync.state === 'running' ? 'RUNNING' : 'IDLE');
    if (stateEl) stateEl.style.color = sync.state === 'stopping' ? '#f59e0b' : sync.state === 'running' ? '#3b82f6' : '#94a3b8';

    setText(EL.syncTrigger, sync.state !== 'idle' ? `Trigger: ${sync.trigger}` : '');

    // ── Counts ──
    setText(EL.totalOutlets, sync.total_outlets);
    setText(EL.inProgress, sync.in_progress > 0 ? `${sync.in_progress} in progress` : '');
    setText(EL.successCount, sync.completed);
    setText(EL.failedCount, sync.failed);
    setText(EL.retryCount, data.retry_queue_size);
    setText(EL.totalCycles, data.total_cycles ?? data.total_syncs_completed);

    // ── Last run ──
    if (sync.started_at) {
        setText(EL.lastRun, formatTime(sync.started_at));
        setText(EL.lastRunTrigger, `Trigger: ${sync.trigger}`);
    }

    // ── Next run countdown ──
    setNextRunTimestamp(data.next_scheduled_run);

    // ── Progress bar ──
    const progressEl = document.getElementById(EL.progressSection);
    const cycleActive = sync.state === 'running' || sync.state === 'stopping';
    progressEl.hidden = !(cycleActive && sync.total_outlets > 0);
    if (cycleActive && sync.total_outlets > 0) {
        const done = sync.completed + sync.failed + (sync.cancelled || 0);
        const pct = Math.round((done / sync.total_outlets) * 100);
        setStyle(EL.progressFill, 'width', `${pct}%`);
        setText(EL.progressText, `${done}/${sync.total_outlets} outlets processed (${pct}%)`);
    }

    // ── Start-sync button ──
    const btn = document.getElementById(EL.btnStartSync);
    if (btn) btn.disabled = cycleActive;
    const stopBtn = document.getElementById(EL.btnStopSync);
    if (stopBtn) stopBtn.disabled = !cycleActive || sync.state === 'stopping';

    // ── Schedule description ──
    if (data.schedule && data.schedule.description) {
        setText(EL.scheduleDesc, `${data.schedule.enabled === false ? 'Paused' : 'Active'}: ${data.schedule.description}`);
        const pause = document.getElementById('btnPauseSchedule');
        const resume = document.getElementById('btnResumeSchedule');
        if (pause) pause.hidden = data.schedule.enabled === false;
        if (resume) resume.hidden = data.schedule.enabled !== false;
    }
}

/** Helper: set innerText on an element by id, return the element. */
function setText(id, val) {
    const el = document.getElementById(id);
    if (el) el.textContent = String(val);
    return el;
}

/** Helper: set a style property on an element. */
function setStyle(id, prop, val) {
    const el = document.getElementById(id);
    if (el) el.style[prop] = val;
}
