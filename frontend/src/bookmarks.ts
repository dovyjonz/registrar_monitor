import { buildTelegramImportText, telegramImportPresentation } from './telegramImport.mjs';
import { bookmarks, summaryCourses } from './state';
import { writeStoredJson } from './safeStorage.mjs';
import { modalBookmarkBtn, telegramBookmarkImport } from './dom';

export const BOOKMARKS_CHANGED_EVENT = 'registrar:bookmarks-changed';

function saveBookmarks() {
    writeStoredJson('courseBookmarks', [...bookmarks]);
    updateTelegramBookmarkImport();
}

function currentSemesterLabel() {
    return document.body.dataset.semester || '';
}

function currentSemesterBookmarks() {
    return [...bookmarks].filter(code => summaryCourses.has(code)).sort();
}

export function updateTelegramBookmarkImport() {
    if (!telegramBookmarkImport) return;
    const selected = currentSemesterBookmarks();
    telegramBookmarkImport.hidden = selected.length === 0;
    const presentation = telegramImportPresentation(selected.length);
    if (!presentation) return;
    telegramBookmarkImport.textContent = presentation.label;
    telegramBookmarkImport.setAttribute('aria-label', presentation.accessibleName);
}

telegramBookmarkImport?.addEventListener('click', async () => {
    try {
        const text = buildTelegramImportText(
            currentSemesterLabel(),
            currentSemesterBookmarks(),
        );
        await navigator.clipboard.writeText(text);
        showToast('Copied. Paste this into Registrar Monitor on Telegram.');
    } catch {
        showToast('Could not copy. Select the text and copy it manually.');
    }
});

export function showToast(message, duration = 4000) {
    const container = document.getElementById('toastContainer');
    if (!container) return;
    const toast = document.createElement('div');
    toast.className = 'toast';
    toast.textContent = message;
    container.appendChild(toast);

    setTimeout(() => {
        toast.classList.add('fade-out');
        setTimeout(() => toast.remove(), 300);
    }, duration);
}

export function updateModalBookmark(code) {
    if (!modalBookmarkBtn) return;
    const isStarred = bookmarks.has(code);
    modalBookmarkBtn.classList.toggle('starred', isStarred);
    modalBookmarkBtn.setAttribute('aria-pressed', String(isStarred));
    modalBookmarkBtn.setAttribute(
        'aria-label',
        isStarred ? 'Remove bookmark' : 'Bookmark course',
    );
    modalBookmarkBtn.onclick = () => {
        if (bookmarks.has(code)) {
            bookmarks.delete(code);
        } else {
            bookmarks.add(code);
        }
        saveBookmarks();
        updateModalBookmark(code);
        // Update course cell
        const cell = document.querySelector<HTMLElement>(`.course-cell[data-course="${CSS.escape(code)}"]`);
        if (cell) {
            cell.classList.toggle('starred', bookmarks.has(code));
        }
        // search.ts re-runs filters (the starred filter reads the cell class);
        // an event avoids a bookmarks -> search import cycle.
        document.dispatchEvent(new CustomEvent(BOOKMARKS_CHANGED_EVENT));
        showToast(bookmarks.has(code) ? 'Course bookmarked' : 'Bookmark removed');
    };
}
