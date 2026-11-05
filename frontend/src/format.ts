
/**
 * Get contrasting text color (black or white) based on background.
 */
export function getContrastColor(hexColor) {
    const hex = hexColor.replace('#', '');
    const r = parseInt(hex.substring(0, 2), 16);
    const g = parseInt(hex.substring(2, 4), 16);
    const b = parseInt(hex.substring(4, 6), 16);
    const luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
    return luminance > 0.5 ? '#1a1a2e' : '#ffffff';
}

/**
 * Get CSS class for fill status.
 */
export function getStatusClass(fill, isFilled = false) {
    if (isFilled || fill >= 1.0) return 'full';
    if (fill >= 0.75) return 'near';
    return '';
}

/**
 * Format ISO date string for display.
 */
export function formatDate(isoString) {
    if (!isoString) return 'N/A';
    const date = new Date(isoString);
    return date.toLocaleDateString('en-US', {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit'
    });
}
