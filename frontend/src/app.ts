import './style.css';
import './miniapp';
import { appState, dataLoadController, departmentPayloads, hydratedCourses, refreshCourseMaps, summaryCourses } from './state';
import { getManifestUrl } from './urlSlugs.mjs';
import { hideTimeoutWarning, showErrorState, showTimeoutWarning } from './search';
import { loadSemesterManifest } from './manifestData.mjs';
import { markAfterAnimationFrames, markPerformance } from './perf';
import { renderMilestoneProgress } from './milestones';
import { renderCourseGrid } from './grid';
import { handleHashNavigation, handleInitialCourseNavigation } from './routing';
import { showToast } from './bookmarks';
// Side-effect imports: these modules register their DOM listeners at load.
import './chart';
import './comparison';
import './events';
import './modal';

function installPayload(payload) {
    appState.DATA = payload.data;
    appState.MILESTONES = payload.milestones || [];
    appState.mappedData = null;
    summaryCourses.clear();
    hydratedCourses.clear();
    departmentPayloads.clear();
    refreshCourseMaps();
}

export async function initApp() {
    // Generated semester pages always start from the v3 manifest pointer.
    const manifestUrl = getManifestUrl(document);
    let summaryReadyMarked = false;
    if (manifestUrl && !appState.DATA) {
        // Show loading timeout warning after 12 seconds
        const timeoutId = setTimeout(showTimeoutWarning, 12000);

        try {
            const absoluteUrl = new URL(manifestUrl, window.location.href);
            const loaded = await loadSemesterManifest(absoluteUrl.href, {
                signal: dataLoadController.signal,
            });
            installPayload(loaded.payload);
            appState.staticManifest = loaded.manifest;
            appState.staticManifestUrl = loaded.manifestUrl;
            appState.staticManifestStale = loaded.stale;
            markPerformance('registrar:summary-ready');
            summaryReadyMarked = true;

            clearTimeout(timeoutId);
            hideTimeoutWarning();
            const loader = document.getElementById('loadingIndicator');
            if (loader) loader.remove();
        } catch (error) {
            console.error("Failed to load enrollment data:", error);
            clearTimeout(timeoutId);
            hideTimeoutWarning();
            const loader = document.getElementById('loadingIndicator');
            if (loader) loader.remove();
            showErrorState();
            return;
        }
    }

    if (!summaryReadyMarked) markPerformance('registrar:summary-ready');

    // Render page-level progress bar
    renderMilestoneProgress();

    // Initial Render
    renderCourseGrid();
    markAfterAnimationFrames('registrar:grid-rendered');

    if (!await handleInitialCourseNavigation()) handleHashNavigation();

    // Show "last updated" toast on load
    setTimeout(() => {
        const lastUpdatedEl = document.getElementById('lastUpdated');
        if (lastUpdatedEl) {
            const text = lastUpdatedEl.textContent;
            const match = text.match(/(\d{1,2}\/\d{1,2}\/\d{2,4}.*)/);
            if (match) {
                showToast(`📊 Data updated: ${match[1]}`);
            }
        }
    }, 1000);
}

// Kick off
initApp();
