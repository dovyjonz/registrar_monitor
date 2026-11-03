import { buildTelegramImportText, telegramImportPresentation } from './telegramImport.mjs';
import { bookmarks, summaryCourses } from './state';
import { modalBookmarkBtn, telegramBookmarkImport } from './dom';

function saveBookmarks() {
    localStorage.setItem('courseBookmarks', JSON.stringify([...bookmarks]));
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
        const cell = document.querySelector<HTMLElement>(`.course-cell[data-course="${code}"]`);
        if (cell) {
            cell.classList.toggle('starred', bookmarks.has(code));
        }
        showToast(bookmarks.has(code) ? 'Course bookmarked' : 'Bookmark removed');
    };
}
