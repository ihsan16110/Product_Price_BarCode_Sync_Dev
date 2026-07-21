import { getLogs, hasApiKey, openLogStream } from '../api.js';

const MAX_LIVE_LINES = 500;
let liveLogBuffer = [];
let streamController = null;
let reconnectTimer = null;

export function init() {
    document.getElementById('btnLoadLogs')?.addEventListener('click', loadArchivedLog);
    document.getElementById('btnReconnectLogs')?.addEventListener('click', connectLiveLogs);
    document.getElementById('btnClearLogs')?.addEventListener('click', clearLive);

    const picker = document.getElementById('logDatePicker');
    if (picker) picker.value = new Date().toISOString().split('T')[0];

    if (hasApiKey()) {
        connectLiveLogs();
        loadArchivedLog();
    } else {
        setStatus('API key required', '#f59e0b');
        const viewer = document.getElementById('logViewer');
        if (viewer) viewer.textContent = 'Enter an API key to load protected logs.';
    }
}

function appendToLiveLog(line) {
    const viewer = document.getElementById('liveLogViewer');
    if (!viewer) return;
    liveLogBuffer.push(String(line));
    if (liveLogBuffer.length > MAX_LIVE_LINES) liveLogBuffer.shift();
    viewer.textContent = liveLogBuffer.join('\n');
    viewer.scrollTop = viewer.scrollHeight;
}

function handleEventBlock(block) {
    let eventName = 'message';
    const data = [];
    for (const line of block.split('\n')) {
        if (line.startsWith('event:')) eventName = line.slice(6).trim();
        if (line.startsWith('data:')) data.push(line.slice(5).trimStart());
    }
    if (eventName === 'connected') {
        setStatus('Connected', '#22c55e');
        appendToLiveLog(`[${new Date().toLocaleTimeString()}] Log stream connected`);
    } else if (data.length) {
        setStatus('Connected', '#22c55e');
        appendToLiveLog(data.join('\n'));
    }
}

async function consumeStream(controller) {
    try {
        const body = await openLogStream(controller.signal);
        const reader = body.getReader();
        const decoder = new TextDecoder();
        let pending = '';
        while (true) {
            const { value, done } = await reader.read();
            pending += decoder.decode(value || new Uint8Array(), { stream: !done }).replace(/\r\n/g, '\n');
            let boundary;
            while ((boundary = pending.indexOf('\n\n')) >= 0) {
                handleEventBlock(pending.slice(0, boundary));
                pending = pending.slice(boundary + 2);
            }
            if (done) break;
        }
        if (!controller.signal.aborted) throw new Error('Log stream ended');
    } catch (error) {
        if (controller.signal.aborted) return;
        setStatus(error.message || 'Disconnected', '#ef4444');
        reconnectTimer = setTimeout(connectLiveLogs, 5000);
    }
}

export function connectLiveLogs() {
    if (reconnectTimer) clearTimeout(reconnectTimer);
    if (streamController) streamController.abort();

    if (!hasApiKey()) {
        setStatus('API key required', '#f59e0b');
        return;
    }

    streamController = new AbortController();
    setStatus('Connecting...', '#f59e0b');
    void consumeStream(streamController);
    void loadArchivedLog();
}

function clearLive() {
    liveLogBuffer = [];
    const viewer = document.getElementById('liveLogViewer');
    if (viewer) viewer.textContent = '';
}

async function loadArchivedLog() {
    const viewer = document.getElementById('logViewer');
    try {
        const picker = document.getElementById('logDatePicker');
        const date = picker?.value || new Date().toISOString().split('T')[0];
        const content = await getLogs(date);
        if (viewer) {
            viewer.textContent = content || 'No logs for this date.';
            viewer.scrollTop = viewer.scrollHeight;
        }
    } catch (error) {
        if (viewer) viewer.textContent = error.message || 'Failed to load logs.';
    }
}

function setStatus(message, color) {
    const status = document.getElementById('liveLogStatus');
    if (!status) return;
    status.textContent = `● ${message}`;
    status.style.color = color;
}

