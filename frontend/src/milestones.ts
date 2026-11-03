import { formatPriorityCompact, formatPriorityFull } from './registrationSemantics.mjs';
import { DEADLINE_LABELS } from './chart';
import { getMilestones } from './state';

/**
 * Render milestone progress bar with equal-spaced dots.
 */
function createMilestoneDot(milestone, index, count, now, renderedPriorities) {
    const startsPriority = milestone.priority
        && !renderedPriorities.has(milestone.priority);
    if (startsPriority) renderedPriorities.add(milestone.priority);

    const dot = document.createElement('div');
    const passed = now >= milestone.time;
    const deadline = DEADLINE_LABELS.has(milestone.label);
    dot.className = `mp-dot${passed ? ' passed' : ''}${startsPriority ? ' priority-start' : ''}${deadline ? ' deadline' : ''}`;
    dot.style.left = `${(index / (count - 1)) * 100}%`;
    if (passed) dot.style.background = milestone.color;
    dot.title = milestone.priority
        ? formatPriorityFull(milestone.priority, milestone.label)
        : milestone.label;

    const label = document.createElement('span');
    label.className = 'mp-dot-label';
    label.textContent = startsPriority
        ? formatPriorityCompact(milestone.priority, milestone.label)
        : milestone.label;
    label.dataset.compactLabel = startsPriority ? `P${milestone.priority}` : milestone.label;
    dot.appendChild(label);
    return dot;
}

export function renderMilestoneProgress() {
    const container = document.getElementById('milestoneProgress');
    if (!container) return;
    const milestones = getMilestones();
    const details = container.closest('.milestone-details');
    const summary = document.getElementById('milestoneSummaryValue');
    if (!milestones || milestones.length < 2) {
        container.textContent = '';
        if (details) (details as HTMLElement).hidden = true;
        return;
    }
    if (details) (details as HTMLElement).hidden = false;

    const now = Date.now();
    const mTimes = milestones.map(m => ({
        time: new Date(m.time).getTime(),
        label: m.label,
        color: m.color,
        priority: m.priority ? String(m.priority) : '',
    })).sort((a, b) => a.time - b.time);
    const count = mTimes.length;
    const nextMilestone = mTimes.find(milestone => now < milestone.time);
    if (summary) {
        const priorityPrefix = nextMilestone?.priority ? `P${nextMilestone.priority} · ` : '';
        summary.textContent = nextMilestone
            ? `${priorityPrefix}Next ${nextMilestone.label}`
            : 'Registration complete';
    }

    // Find how far along we are in terms of milestone segments passed
    let filledSegments = 0;
    for (let i = 0; i < count; i++) {
        if (now >= mTimes[i].time) filledSegments = i + 1;
    }
    // Interpolate within current segment
    let fillPct;
    if (filledSegments >= count) {
        fillPct = 100;
    } else if (filledSegments === 0) {
        fillPct = 0;
    } else {
        const segStart = mTimes[filledSegments - 1].time;
        const segEnd = mTimes[filledSegments].time;
        const segFrac = segEnd === segStart ? 1 : (now - segStart) / (segEnd - segStart);
        fillPct = ((filledSegments - 1 + Math.min(1, Math.max(0, segFrac))) / (count - 1)) * 100;
    }

    container.textContent = '';
    const track = document.createElement('div');
    track.className = 'mp-track';

    const fill = document.createElement('div');
    fill.className = 'mp-fill';
    const gradientStops = mTimes.map((milestone, index) => {
        const position = (index / (count - 1)) * 100;
        return `${milestone.color || '#78909C'} ${position}%`;
    });
    fill.style.background = `linear-gradient(90deg, ${gradientStops.join(', ')})`;
    // Reveal the full-width gradient instead of scaling it. Scaling compresses
    // the color stops during active registration, so dots no longer match the
    // line color at their physical timeline position.
    fill.style.clipPath = `inset(0 ${100 - fillPct}% 0 0)`;
    track.appendChild(fill);

    const renderedPriorities = new Set();
    for (let i = 0; i < count; i++) {
        track.appendChild(createMilestoneDot(
            mTimes[i], i, count, now, renderedPriorities,
        ));
    }

    container.appendChild(track);
}
