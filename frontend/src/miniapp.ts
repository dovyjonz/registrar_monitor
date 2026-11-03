// Telegram Mini App: when the dashboard is opened from Telegram, show Watch and
// Unwatch buttons in the course modal. The bot's own API (website.miniapp_api_url)
// authenticates each call with Telegram's signed initData; no user id is sent.
import { appState, getCurrentSemesterLabel } from './state';
import { showToast } from './bookmarks';
import {
    buildApiRequest,
    errorMessage,
    initDataFromHash,
    isTelegramLaunch,
    normalizeApiUrl,
    watchControls,
} from './miniappApi.mjs';

const TELEGRAM_SCRIPT = 'https://telegram.org/js/telegram-web-app.js';

// Telegram reads its launch parameters from the URL hash when its script loads,
// and the router rewrites the URL later, so capture the hash first.
const launchHash = window.location.hash;

let session: { apiUrl: string; initData: string } | null = null;
let watchState: { semester: string | null; watches: any[] } | null = null;
let busy = false;

function loadTelegramScript(): Promise<void> {
    return new Promise((resolve, reject) => {
        const script = document.createElement('script');
        script.src = TELEGRAM_SCRIPT;
        script.onload = () => resolve();
        script.onerror = () => reject(new Error('Telegram script failed to load'));
        document.head.appendChild(script);
    });
}

async function callApi(method: string, path: string, body?: unknown) {
    const { url, init } = buildApiRequest(session.apiUrl, session.initData, method, path, body);
    const response = await fetch(url, init as RequestInit);
    if (!response.ok) throw new Error(errorMessage(response.status));
    return response.json();
}

export function renderWatchControls() {
    const bar = document.getElementById('watchBar');
    if (!bar) return;
    bar.textContent = '';
    const controls = watchState && watchControls({
        watches: watchState.watches,
        activeSemester: watchState.semester,
        semester: getCurrentSemesterLabel(),
        course: appState.selectedCourse,
        section: appState.selectedSection,
    });
    bar.hidden = !controls;
    for (const item of controls || []) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = `watch-btn${item.action === 'remove' ? ' unwatch' : ''}`;
        button.textContent = item.label;
        button.disabled = busy || item.disabled;
        button.onclick = () => void applyControl(item);
        bar.appendChild(button);
        if (item.hint) {
            const hint = document.createElement('span');
            hint.className = 'watch-hint';
            hint.textContent = item.hint;
            bar.appendChild(hint);
        }
    }
}

async function applyControl(item) {
    busy = true;
    renderWatchControls();
    try {
        const result = item.action === 'add'
            ? await callApi('POST', '/watches', item.target)
            : await callApi('DELETE', `/watches/${encodeURIComponent(item.id)}`);
        watchState = { semester: result.semester, watches: result.watches };
        showToast(item.action === 'add' ? 'Watching. You will get a Telegram message when it changes.' : 'Watch removed');
    } catch (error) {
        showToast(error instanceof Error ? error.message : 'Could not update your watches.');
    } finally {
        busy = false;
        renderWatchControls();
    }
}

async function startMiniApp() {
    const apiUrl = normalizeApiUrl(
        document.querySelector<HTMLMetaElement>('meta[name="miniapp-api"]')?.content,
    );
    // Normal visitors never load Telegram's script.
    if (!apiUrl || !isTelegramLaunch(launchHash)) return;
    try {
        await loadTelegramScript();
        const webApp = (window as any).Telegram?.WebApp;
        const initData = webApp?.initData || initDataFromHash(launchHash);
        if (!initData) return;
        webApp.ready();
        webApp.expand();
        session = { apiUrl, initData };
        const { semester, watches } = await callApi('GET', '/watches');
        watchState = { semester, watches };
        renderWatchControls();
    } catch (error) {
        console.error('Telegram Mini App unavailable:', error);
    }
}

void startMiniApp();
