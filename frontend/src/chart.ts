import { OBSERVATION_STEP_MODE, buildAverageChartPoints, buildChartPresentation, buildCourseChartDomain, buildObservedCapacityPoints, buildRegistrationUnavailableIntervals, buildSectionChartPoints, createHistoricalCoordinateMapper, extendSteppedSeriesToDomainEnd, filterMilestonesToObservedRange, findSteppedIndex, getChartMapper, getEnrollmentScaleMax, getXScaleBounds, limitPointsAroundMilestones, markObservationEnd } from './chartMapping.mjs';
import { formatPriorityCompact, formatPriorityFull } from './registrationSemantics.mjs';
import { appState, getCourseSections, getCourseSnapshots, getHydratedCourse, getMilestones } from './state';
import { getContrastColor } from './format';
import { enableHistoricalComparison, initializeHistoricalComparisonControl, mapHistoricalComparisonPoints, resetHistoricalComparisonState, resolveHistoricalAvailability, updateHistoricalLegend } from './comparison';
import { renderWatchControls } from './miniapp';

let chartJsLoading: Promise<void> | null = null;

function loadChartJs(): Promise<void> {
    if (appState.chartJsLoaded) return Promise.resolve();
    chartJsLoading ??= setupChartJs().catch(error => {
        chartJsLoading = null;
        throw error;
    });
    return chartJsLoading;
}

/** Start fetching the Chart.js chunk early; failures surface on first render. */
export function preloadChartJs() {
    loadChartJs().catch(() => {});
}

function sameActiveElements(first, second) {
    return first.length === second.length && first.every((item, position) => (
        item.datasetIndex === second[position].datasetIndex
        && item.index === second[position].index
    ));
}

function getSteppedIndexAtX(meta, pixelX, useFinalPosition = false) {
    return findSteppedIndex(
        meta.data.length,
        index => meta.data[index].getProps(['x'], useFinalPosition).x,
        pixelX,
    );
}

async function setupChartJs() {
    const [
        { Chart, Interaction, Tooltip },
        chartHelpers,
        annotationModule,
        zoomModule,
    ] = await Promise.all([
        import('./chartJsCore'),
        import('chart.js/helpers'),
        import('chartjs-plugin-annotation'),
        import('chartjs-plugin-zoom'),
    ]);
    appState.Chart = Chart;
    (Tooltip.positioners as any).glide = function(items, eventPosition) {
        const average = Tooltip.positioners.average.call(this, items, eventPosition);
        if (average === false) return false;
        return {
            x: Math.min(Math.max(eventPosition.x, this.chart.chartArea.left), this.chart.chartArea.right),
            y: average.y,
        };
    };
    const getSteppedElementAtX = (meta, pixelX, useFinalPosition = false) => {
        const index = getSteppedIndexAtX(meta, pixelX, useFinalPosition);
        return index === null ? null : { element: meta.data[index], index };
    };
    (Interaction.modes as any).glideStepped = (chartInstance, event, options, useFinalPosition) => {
        const position = chartHelpers.getRelativePosition(event, chartInstance);
        return chartInstance.data.datasets.flatMap((_, datasetIndex) => {
            const meta = chartInstance.getDatasetMeta(datasetIndex);
            if (!meta.visible) return [];
            const selected = getSteppedElementAtX(meta, position.x, useFinalPosition);
            return selected ? [{
                element: selected.element,
                datasetIndex,
                index: selected.index,
            }] : [];
        });
    };
    const getPinnedHoverPixel = chartInstance => (
        chartInstance.$tooltipPinned && Number.isFinite(chartInstance.$pinnedPixelX)
            ? chartInstance.$pinnedPixelX
            : null
    );
    const resolveHoverPixel = (chartInstance, event, options) => {
        const nativeEvent = event.native;
        const isTouch = nativeEvent?.pointerType === 'touch'
            || nativeEvent?.touches !== undefined
            || nativeEvent?.changedTouches !== undefined;
        const { chartArea } = chartInstance;
        const insideChart = event.type !== 'mouseout'
            && event.x >= chartArea.left && event.x <= chartArea.right
            && event.y >= chartArea.top && event.y <= chartArea.bottom;
        const domainX = chartInstance.scales.x.getValueForPixel(event.x);
        const insideObservedX = Number.isFinite(domainX)
            && domainX >= options.observedMinX
            && domainX <= options.observedMaxX;
        if (!isTouch) {
            chartInstance.$lastInputWasTouch = false;
            const pinnedPixel = getPinnedHoverPixel(chartInstance);
            if (Number.isFinite(pinnedPixel)) return pinnedPixel;
            return insideChart && insideObservedX ? event.x : null;
        }

        chartInstance.$lastInputWasTouch = true;
        if (chartInstance.canvas.dataset.touchMode === 'pan'
            && Number.isFinite(chartInstance.$hoverPixelX)) {
            return chartInstance.$hoverPixelX;
        }
        if (insideObservedX) {
            return event.x;
        }
        return chartInstance.$hoverPixelX ?? null;
    };
    const hoverIndicator = {
        id: 'hoverIndicator',
        afterEvent(chartInstance, args, options) {
            const { event } = args;
            const nextX = resolveHoverPixel(chartInstance, event, options);
            if (chartInstance.$tooltipPinned && Number.isFinite(chartInstance.$pinnedPixelX)) {
                const hoverMoved = chartInstance.$hoverPixelX !== chartInstance.$pinnedPixelX;
                const previousActive = chartInstance.tooltip?.getActiveElements() ?? [];
                chartInstance.$hoverPixelX = chartInstance.$pinnedPixelX;
                refreshChartTooltip(chartInstance);
                const nextActive = chartInstance.tooltip?.getActiveElements() ?? [];
                if (hoverMoved || !sameActiveElements(previousActive, nextActive)) {
                    args.changed = true;
                }
                return;
            }
            if (chartInstance.$hoverPixelX !== nextX) {
                chartInstance.$hoverPixelX = nextX;
                if (Number.isFinite(nextX) && typeof options.unmapX === 'function') {
                    const mappedX = chartInstance.scales.x.getValueForPixel(nextX);
                    chartInstance.canvas.dataset.hoverTimestamp = String(options.unmapX(mappedX));
                } else {
                    delete chartInstance.canvas.dataset.hoverTimestamp;
                }
                if (Number.isFinite(nextX)) refreshChartTooltip(chartInstance);
                args.changed = true;
            }
        },
        afterDatasetsDraw(chartInstance) {
            const pixelX = chartInstance.$hoverPixelX;
            if (!Number.isFinite(pixelX)) return;
            const { ctx } = chartInstance;
            ctx.save();
            ctx.beginPath();
            ctx.moveTo(pixelX, chartInstance.chartArea.top);
            ctx.lineTo(pixelX, chartInstance.chartArea.bottom);
            ctx.lineWidth = 1;
            ctx.strokeStyle = 'rgba(220, 224, 232, 0.32)';
            ctx.stroke();
            chartInstance.data.datasets.forEach((dataset, datasetIndex) => {
                const meta = chartInstance.getDatasetMeta(datasetIndex);
                if (!meta.visible) return;
                const selected = getSteppedElementAtX(meta, pixelX);
                if (!selected) return;
                const { y } = selected.element.getProps(['y'], false);
                if (!Number.isFinite(y)) return;
                ctx.beginPath();
                ctx.arc(pixelX, y, dataset.id === 'enrollment' ? 4 : 3, 0, Math.PI * 2);
                ctx.fillStyle = '#1f3b60';
                ctx.fill();
                ctx.lineWidth = 2;
                ctx.strokeStyle = dataset.borderColor;
                ctx.stroke();
            });
            ctx.restore();
        },
    };
    appState.Chart.register(annotationModule.default, zoomModule.default, hoverIndicator);
    appState.chartJsLoaded = true;
}

export const DEADLINE_LABELS = new Set(['Drop', 'WL', 'Close']);

function getTimestampBounds(timestamps) {
    if (timestamps.length === 0) return null;
    let min = Infinity;
    let max = -Infinity;
    for (const timestamp of timestamps) {
        if (timestamp < min) min = timestamp;
        if (timestamp > max) max = timestamp;
    }
    return { min, max };
}

function shouldRenderMilestone(milestone, chartMode, dataBounds) {
    if (chartMode !== 'phased' && DEADLINE_LABELS.has(milestone.label)) return false;
    const milestoneTime = new Date(milestone.time).getTime();
    if (!Number.isFinite(milestoneTime)) return false;
    if (chartMode !== 'phased' && dataBounds
        && (milestoneTime < dataBounds.min || milestoneTime > dataBounds.max)) {
        return false;
    }
    return true;
}

function addCurrentMilestoneAnnotations({
    annotations,
    milestones,
    chartMode,
    dataBounds,
    mapTime,
    compactLabels,
}: any) {
    const renderedPriorities = new Set();
    milestones.forEach((milestone, index) => {
        if (!shouldRenderMilestone(milestone, chartMode, dataBounds)) return;
        const xPos = mapTime(new Date(milestone.time).getTime());
        if (!Number.isFinite(xPos)) return;

        const priority = milestone.priority ? String(milestone.priority) : '';
        const startsPriority = priority && !renderedPriorities.has(priority);
        if (startsPriority) renderedPriorities.add(priority);
        const milestoneLabel = startsPriority
            ? compactLabels ? `P${priority}` : formatPriorityCompact(priority, milestone.label)
            : milestone.label;
        const displayLabel = !compactLabels || startsPriority
            || DEADLINE_LABELS.has(milestone.label);

        const color = milestone.color || '#78909C';

        annotations[`line${index}`] = {
            type: 'line', xMin: xPos, xMax: xPos,
            borderColor: color, borderWidth: 2, borderDash: [5, 3],
            drawTime: 'beforeDatasetsDraw',
            z: -1,
            label: {
                display: displayLabel, content: milestoneLabel, position: 'end',
                backgroundColor: color, color: getContrastColor(color),
                font: { size: 9, weight: 'bold' }, padding: 3, borderRadius: 3,
                // Keep the line behind the enrollment series, but draw the
                // label afterward so its text and background stay opaque.
                z: 10, drawTime: 'afterDatasetsDraw',
            }
        };
    });
}

function buildHistoricalChartState({
    historicalComparison,
    chartMode,
    currentComparisonDomain,
    currentMapper,
    milestones,
}) {
    if (!historicalComparison?.chartPoints?.length) {
        return { historicalDataPoints: [] };
    }

    const sourceHistoricalChartPoints = historicalComparison.chartPoints
        .map((point, sourceIndex) => ({
            ...point,
            sourceIndex,
        }));
    const historicalChartPoints = limitPointsAroundMilestones(
        sourceHistoricalChartPoints,
        historicalComparison.milestones,
    );
    const sourceEnd = sourceHistoricalChartPoints.at(-1);
    if (sourceEnd?.removalEnded
        && !historicalChartPoints.some(point => point.sourceIndex === sourceEnd.sourceIndex)) {
        historicalChartPoints.push(sourceEnd);
        historicalChartPoints.sort((first, second) => first.timestamp - second.timestamp);
    }
    const historicalChartDomain = limitPointsAroundMilestones(
        historicalComparison.chartDomain,
        historicalComparison.milestones,
    );

    const historicalMapper = getChartMapper(
        chartMode,
        historicalChartPoints,
        historicalChartDomain,
        historicalComparison.milestones,
    );
    const historicalDomain = historicalMapper.domainXValues.length > 0
        ? historicalMapper.domainXValues
        : historicalMapper.xValues;
    // A current semester may have only its first observation. Keep the
    // historical overlay drawable until the current domain has two anchors.
    const targetDomain = currentComparisonDomain.length > 1
        ? currentComparisonDomain
        : historicalDomain;
    const alignMilestones = chartMode === 'phased' || currentComparisonDomain.length > 1;
    const historicalCoordinateMapper = createHistoricalCoordinateMapper({
        historicalDomainXValues: historicalDomain,
        currentDomainXValues: targetDomain,
        historicalMilestones: alignMilestones ? historicalComparison.milestones : [],
        currentMilestones: alignMilestones ? milestones : [],
        historicalMapTime: historicalMapper.mapTime,
        currentMapTime: currentMapper.mapTime,
    });

    const historicalDataPoints = mapHistoricalComparisonPoints(
            { ...historicalComparison, chartPoints: historicalChartPoints },
            historicalMapper,
            historicalCoordinateMapper,
        );
    return { historicalDataPoints };
}

function buildMilestoneRenderState({
    chartMode,
    milestones,
    domainTimestamps,
    mapTime,
    compactLabels,
}) {
    const dataBounds = getTimestampBounds(domainTimestamps);
    const annotationXValues = chartMode === 'phased'
        ? milestones
            .filter(milestone => shouldRenderMilestone(milestone, chartMode, dataBounds))
            .map(milestone => mapTime(new Date(milestone.time).getTime()))
            .filter(Number.isFinite)
        : [];
    const annotations = {};
    if (milestones.length > 0
        && (chartMode === 'phased' || domainTimestamps.length > 0)) {
        addCurrentMilestoneAnnotations({
            annotations,
            milestones,
            chartMode,
            dataBounds,
            mapTime,
            compactLabels,
        });
    }
    return { annotations, annotationXValues };
}

function addRegistrationUnavailableAnnotations(
    annotations,
    visibleChartPoints,
    xValues,
    domainEnd,
) {
    const intervals = buildRegistrationUnavailableIntervals(
        visibleChartPoints.map((point, index) => ({
            x: xValues[index],
            registrationUnavailable: point.registrationUnavailable,
            limitingTypes: point.limitingTypes,
        })),
        domainEnd,
    );
    intervals.forEach((interval, index) => {
        annotations[`registrationUnavailable${index}`] = {
            type: 'box',
            xMin: interval.xMin,
            xMax: interval.xMax,
            backgroundColor: 'rgba(239, 68, 68, 0.13)',
            borderColor: 'rgba(239, 68, 68, 0.42)',
            borderWidth: 1,
            drawTime: 'beforeDatasetsDraw',
            z: -2,
            label: { display: false },
        };
    });
    return intervals;
}

export function setZoomControlsState(isZoomed) {
    const resetBtn = document.getElementById('chartZoomReset');
    if (resetBtn) {
        (resetBtn as HTMLButtonElement).disabled = !appState.chart || !isZoomed;
    }
}

function updateZoomControls() {
    const isZoomed = appState.chart && typeof appState.chart.isZoomedOrPanned === 'function'
        ? appState.chart.isZoomedOrPanned()
        : false;
    setZoomControlsState(Boolean(isZoomed));
}

function resetChartZoom() {
    if (appState.chart && typeof appState.chart.resetZoom === 'function') {
        appState.chart.resetZoom('none');
        syncChartViewportState(
            appState.chart,
            appState.chart.canvas,
            window.matchMedia('(pointer: coarse)').matches,
        );
    }
    updateZoomControls();
}

function syncChartViewportState(chartInstance, canvas, coarsePointer) {
    const isZoomed = Boolean(chartInstance?.isZoomedOrPanned?.());
    canvas.dataset.touchMode = coarsePointer && isZoomed
        ? 'pan'
        : coarsePointer ? 'inspect' : 'hover';
    canvas.parentElement.dataset.gestureMode = canvas.dataset.touchMode;
    if (chartInstance.$tooltipPinned
        && Number.isFinite(chartInstance.$hoverPixelX)
        && typeof chartInstance.$unmapHoverX === 'function') {
        const mappedX = chartInstance.scales.x.getValueForPixel(chartInstance.$hoverPixelX);
        canvas.dataset.hoverTimestamp = String(chartInstance.$unmapHoverX(mappedX));
        chartInstance.$refreshPinnedReadout = true;
    }
    refreshChartTooltip(chartInstance);
    if (chartInstance.$tooltipPinned) chartInstance.draw();
    updateZoomControls();
    if (!coarsePointer) return;

    const shouldPan = isZoomed;
    if (chartInstance.options.plugins.zoom.pan.enabled !== shouldPan) {
        chartInstance.options.plugins.zoom.pan.enabled = shouldPan;
        queueMicrotask(() => {
            if (chartInstance.canvas?.isConnected) chartInstance.update('none');
        });
    }
}

function refreshChartTooltip(chartInstance) {
    const pixelX = chartInstance?.$hoverPixelX;
    if (!Number.isFinite(pixelX) || !chartInstance.tooltip) return;
    const forceReadoutRefresh = Boolean(chartInstance.$refreshPinnedReadout);
    const active = chartInstance.data.datasets.flatMap((_, datasetIndex) => {
        const meta = chartInstance.getDatasetMeta(datasetIndex);
        if (!meta.visible) return [];
        const index = getSteppedIndexAtX(meta, pixelX);
        return index === null ? [] : [{ datasetIndex, index }];
    });
    chartInstance.tooltip.setActiveElements(active, {
        x: pixelX,
        y: chartInstance.chartArea.top + chartInstance.chartArea.height / 2,
    });
    if (forceReadoutRefresh && chartInstance.$refreshPinnedReadout) {
        chartInstance.tooltip.update(true);
    }
}

function createMilestoneReference(milestones) {
    const ordered = milestones
        .map(milestone => ({ milestone, time: new Date(milestone.time).getTime() }))
        .filter(entry => Number.isFinite(entry.time))
        .sort((first, second) => first.time - second.time);
    return (timestamp, compact = false) => {
        if (!Number.isFinite(timestamp)) return '';
        const nextIndex = ordered.findIndex(entry => entry.time > timestamp);
        const previousEntry = nextIndex === -1 ? ordered.at(-1) : ordered[nextIndex - 1];
        const nextEntry = nextIndex === -1 ? undefined : ordered[nextIndex];
        const previous = previousEntry && formatMilestoneReference(previousEntry.milestone, compact);
        const next = nextEntry && formatMilestoneReference(nextEntry.milestone, compact);
        if (previous && next) return `${previous} → ${next}`;
        if (next) return `Until ${next}`;
        if (previous) return `Since ${previous}`;
        return '';
    };
}

function formatMilestoneReference(milestone, compact = false) {
    if (compact) {
        return milestone.priority
            ? formatPriorityCompact(milestone.priority, milestone.label)
            : milestone.label;
    }
    if (DEADLINE_LABELS.has(milestone.label)) return `${milestone.label} deadline`;
    const yearMatch = /^Y(.+)$/u.exec(milestone.label);
    const label = yearMatch ? `Year ${yearMatch[1]}` : milestone.label;
    return milestone.priority ? formatPriorityFull(milestone.priority, milestone.label) : label;
}

function setChartTooltipPinned(chartInstance, pixelX) {
    if (!chartInstance || !Number.isFinite(pixelX)) return;
    const wasPinnedHere = chartInstance.$tooltipPinned
        && Number.isFinite(chartInstance.$pinnedPixelX)
        && Math.abs(chartInstance.$pinnedPixelX - pixelX) < 8;
    if (wasPinnedHere) {
        chartInstance.canvas.dataset.tooltipPinned = 'false';
        queueMicrotask(clearChartActiveElements);
        return;
    }
    chartInstance.$tooltipPinned = true;
    chartInstance.$pinnedPixelX = pixelX;
    chartInstance.$hoverPixelX = pixelX;
    chartInstance.$pinnedViewportMin = chartInstance.scales.x.min;
    chartInstance.canvas.dataset.tooltipPinned = 'true';
    refreshChartTooltip(chartInstance);
    showPinnedReadoutBadge();
    chartInstance.draw();
}

function showPinnedReadoutBadge() {
    const header = document.querySelector<HTMLElement>('#chartReadout .chart-readout-header');
    if (!header || header.querySelector<HTMLElement>('.chart-readout-pinned')) return;
    const pinned = document.createElement('span');
    pinned.className = 'chart-readout-pinned';
    pinned.textContent = 'Pinned';
    header.appendChild(pinned);
}

function hasPinnedViewportMoved(chartInstance, viewportMin) {
    return chartInstance.$tooltipPinned
        && Number.isFinite(viewportMin)
        && viewportMin !== chartInstance.$pinnedViewportMin;
}

function shouldFreezePinnedReadout(chartInstance, viewportMin, readout) {
    return chartInstance.$tooltipPinned
        && !hasPinnedViewportMoved(chartInstance, viewportMin)
        && !chartInstance.$refreshPinnedReadout
        && !readout.hidden
        && readout.childElementCount > 0;
}

function renderChartReadout({ chart: chartInstance, tooltip }, canvas, getMilestoneReference) {
    const readout = document.getElementById('chartReadout');
    if (!readout) return;
    const viewportMin = chartInstance.scales.x?.min;
    const freezePinnedReadout = shouldFreezePinnedReadout(chartInstance, viewportMin, readout);
    if (freezePinnedReadout) return;
    chartInstance.$refreshPinnedReadout = false;
    if (tooltip.opacity === 0) {
        if (!chartInstance.$tooltipPinned) readout.hidden = true;
        return;
    }

    readout.textContent = '';
    const header = document.createElement('span');
    header.className = 'chart-readout-header';
    const title = document.createElement('span');
    title.className = 'chart-readout-title';
    title.textContent = tooltip.title?.[0] || '';
    header.appendChild(title);
    const compactMilestones = window.matchMedia('(width <= 768px)').matches;
    const context = getMilestoneReference(
        Number(canvas.dataset.hoverTimestamp),
        compactMilestones,
    );
    if (context) {
        const contextElement = document.createElement('span');
        contextElement.className = 'chart-readout-context';
        contextElement.textContent = context;
        header.appendChild(contextElement);
    }
    if (chartInstance.$tooltipPinned) {
        const pinned = document.createElement('span');
        pinned.className = 'chart-readout-pinned';
        pinned.textContent = 'Pinned';
        header.appendChild(pinned);
    }
    readout.appendChild(header);
    const values = document.createElement('span');
    values.className = 'chart-readout-values';
    for (const item of tooltip.body || []) {
        for (const lineText of item.lines || []) {
            const line = document.createElement('span');
            line.className = 'chart-readout-line';
            const separator = lineText.indexOf(':');
            const label = document.createElement('span');
            label.className = 'chart-readout-label';
            label.textContent = separator >= 0 ? lineText.slice(0, separator) : lineText;
            const value = document.createElement('span');
            value.className = 'chart-readout-value';
            value.textContent = separator >= 0 ? lineText.slice(separator + 1).trim() : '';
            line.append(label, value);
            values.appendChild(line);
        }
    }
    readout.appendChild(values);
    readout.hidden = false;
    if (chartInstance.$tooltipPinned) chartInstance.$pinnedViewportMin = viewportMin;
}

function selectChartScaleDomain(currentDomain, historicalDomain) {
    if (currentDomain.length > 1) return currentDomain;
    if (historicalDomain.length > 1) return historicalDomain;
    return currentDomain;
}

function replaceActiveChart() {
    if (!appState.chart) return;
    const { canvas } = appState.chart;
    appState.chart.destroy();
    appState.chart = null;
    // A pinned readout belongs to the destroyed chart; do not let it outlive it.
    delete canvas.dataset.hoverTimestamp;
    const readout = document.getElementById('chartReadout');
    if (readout) {
        readout.hidden = true;
        readout.textContent = '';
    }
}

/**
 * Show average fill chart for a course.
 */
export async function showAverageFillChart(
    courseCode,
    course = getHydratedCourse(courseCode),
    requestVersion = null,
) {
    if (requestVersion !== null && requestVersion !== appState.courseRequestVersion) return;
    if (!course) return;
    const snapshots = getCourseSnapshots(course);
    const chartPoints = buildAverageChartPoints(course, snapshots);
    const chartDomain = buildCourseChartDomain(course, snapshots);
    appState.currentEnrollmentData = chartPoints;

    updateHistoricalLegend();
    await renderChart(courseCode, chartPoints, chartDomain, { requestVersion });
}

function updateChartAccessibilitySummary({
    canvas,
    chartLabel,
    visibleChartPoints,
    fillData,
    historicalComparison,
    hasHistoricalDataset,
    registrationUnavailableIntervals,
}) {
    canvas.setAttribute('aria-label', `${chartLabel} enrollment history`);
    const summary = document.getElementById('chartSummary');
    if (!summary) return;

    const latestPoint = visibleChartPoints.at(-1);
    const latestFill = Number.isFinite(latestPoint?.fill)
        ? Math.round(latestPoint.fill)
        : Math.round(fillData.at(-1) || 0);
    const countSummary = Number.isFinite(latestPoint?.enrollment)
        && Number.isFinite(latestPoint?.capacity)
        ? `, ${latestPoint.enrollment} of ${latestPoint.capacity} seats filled`
        : '';
    const professorSummary = historicalComparison?.mode === 'professor'
        ? ` for ${historicalComparison.professorDisplayName}`
        : '';
    const comparisonSummary = hasHistoricalDataset
        ? ` Compared with ${historicalComparison.semester}${professorSummary}.`
        : '';
    const unavailableSummary = registrationUnavailableIntervals.length > 0
        ? ` ${registrationUnavailableIntervals.length} registration-unavailable interval${registrationUnavailableIntervals.length === 1 ? '' : 's'}.`
        : '';
    summary.textContent = `${chartLabel}. ${visibleChartPoints.length} enrollment observations. Latest: ${latestFill}% full${countSummary}.${unavailableSummary}${comparisonSummary}`;
}

function updateRegistrationUnavailableGuide(intervals) {
    const guide = document.getElementById('registrationUnavailableGuide');
    if (guide) guide.hidden = intervals.length === 0;
}

/**
 * Select a section and show its enrollment chart.
 */
export async function selectSection(sectionCode) {
    const course = getHydratedCourse(appState.selectedCourse);
    if (!course) return;
    const requestVersion = appState.courseRequestVersion;

    // Toggle selection if clicking same section
    if (appState.selectedSection === sectionCode) {
        const selectedItem = document.getElementById(`section-${sectionCode}`);
        selectedItem?.classList.remove('selected');
        selectedItem?.setAttribute('aria-pressed', 'false');
        appState.selectedSection = null;
        renderWatchControls();
        resetHistoricalComparisonState();
        initializeHistoricalComparisonControl(course);
        appState.currentEnrollmentData = [];
        await showAverageFillChart(appState.selectedCourse, course, requestVersion);
        void resolveHistoricalAvailability(appState.selectedCourse, course, requestVersion);
        return;
    }

    // Update selection styling
    if (appState.selectedSection) {
        const previousItem = document.getElementById(`section-${appState.selectedSection}`);
        previousItem?.classList.remove('selected');
        previousItem?.setAttribute('aria-pressed', 'false');
    }
    appState.selectedSection = sectionCode;
    renderWatchControls();
    resetHistoricalComparisonState();
    const selectedItem = document.getElementById(`section-${sectionCode}`);
    selectedItem?.classList.add('selected');
    selectedItem?.setAttribute('aria-pressed', 'true');

    const section = getCourseSections(course)[sectionCode];
    if (!section) return;
    initializeHistoricalComparisonControl(course);

    const snapshots = getCourseSnapshots(course);
    const chartPoints = buildSectionChartPoints(section, snapshots);
    const chartDomain = buildCourseChartDomain(course, snapshots);
    appState.currentEnrollmentData = chartPoints;

    updateHistoricalLegend();

    await renderChart(
        `${sectionCode} Enrollment %`,
        chartPoints,
        chartDomain,
        { requestVersion },
    );
    void resolveHistoricalAvailability(appState.selectedCourse, course, requestVersion);
}

/**
 * Render enrollment chart with milestones and phased/timeline/snapshots mode.
 */
export async function renderChart(
    chartLabel,
    chartPoints,
    chartDomain,
    { requestVersion = null, historicalComparison = null } = {},
) {
    await loadChartJs();
    if (requestVersion !== null && requestVersion !== appState.courseRequestVersion) return;
    // Cache args for mode toggle re-render
    appState.lastRenderArgs = {
        chartLabel,
        chartPoints,
        chartDomain,
        requestVersion,
        historicalComparison,
    };

    const milestones = document.body.dataset.pageArchived === 'true'
        ? filterMilestonesToObservedRange(getMilestones(), chartDomain)
        : getMilestones();
    const presentation = buildChartPresentation({
        points: chartPoints,
        domain: chartDomain,
        milestones,
        mode: appState.chartMode,
    });
    const {
        visiblePoints: visibleChartPoints,
        visibleDomain: visibleChartDomain,
        enrollmentValues: fillData,
        capacityValues: capacityData,
        xValues,
        domainXValues,
        mapTime,
        unmapX,
    } = presentation;
    const domainTimestamps = visibleChartDomain.map(point => point.timestamp);
    const currentMapper = { xValues, domainXValues, mapTime, unmapX };
    const currentComparisonDomain = domainXValues.length > 0 ? domainXValues : xValues;
    const recordedDomainEnd = currentComparisonDomain.at(-1);
    const historicalChartState = buildHistoricalChartState({
        historicalComparison,
        chartMode: appState.chartMode,
        currentComparisonDomain,
        currentMapper,
        milestones,
    });
    const { historicalDataPoints } = historicalChartState;
    const hasHistoricalDataset = historicalDataPoints.length > 0;
    const enrollmentScaleMax = getEnrollmentScaleMax([
        ...fillData,
        ...capacityData,
        ...historicalDataPoints.map(point => point.y),
    ]);
    const getMilestoneReference = createMilestoneReference(milestones);
    const canvas = document.getElementById('enrollment-chart');
    const compactMilestoneLabels = canvas.parentElement.clientWidth < 520;
    const { annotations, annotationXValues } = buildMilestoneRenderState({
        chartMode: appState.chartMode,
        milestones,
        domainTimestamps,
        mapTime,
        compactLabels: compactMilestoneLabels,
    });
    const registrationUnavailableIntervals = addRegistrationUnavailableAnnotations(
        annotations,
        visibleChartPoints,
        xValues,
        recordedDomainEnd,
    );
    updateRegistrationUnavailableGuide(registrationUnavailableIntervals);
    const historicalXValues = historicalDataPoints.map(point => point.x);
    const xScaleDomain = selectChartScaleDomain(currentComparisonDomain, historicalXValues);
    const xBounds = getXScaleBounds([
        ...xScaleDomain,
        ...annotationXValues,
    ]);
    const displayHistoricalDataPoints = historicalDataPoints.at(-1)?.removalEnded
        ? historicalDataPoints
        : markObservationEnd(historicalDataPoints);

    // Show chart canvas
    document.getElementById('chartPlaceholder').style.display = 'none';
    canvas.classList.remove('chart-hidden');
    updateChartAccessibilitySummary({
        canvas,
        chartLabel,
        visibleChartPoints,
        fillData,
        historicalComparison,
        hasHistoricalDataset,
        registrationUnavailableIntervals,
    });
    canvas.dataset.registrationUnavailableIntervals = String(
        registrationUnavailableIntervals.length,
    );

    replaceActiveChart();
    setZoomControlsState(false);

    // Build dataset with {x, y} pairs
    const dataPoints = fillData.map((y, i) => ({
        x: xValues[i],
        y,
        sourceIndex: i,
        limitingTypes: visibleChartPoints[i]?.limitingTypes ?? [],
    }));
    const capacityDataPoints = buildObservedCapacityPoints(
        capacityData,
        xValues,
        visibleChartPoints,
    );
    const displayDataPoints = extendSteppedSeriesToDomainEnd(dataPoints, recordedDomainEnd);
    const displayCapacityDataPoints = extendSteppedSeriesToDomainEnd(
        capacityDataPoints,
        recordedDomainEnd,
    );

    // Regular and hover point radii stay zero on every dataset so the lines read
    // as steps; only synthetic and observation-ended markers get a radius.
    // Capacity-change markers stay visible, and horizontal guides render behind
    // datasets with a negative z-order.
    const currentDataset = {
        id: 'enrollment',
        label: chartLabel,
        data: displayDataPoints,
        borderColor: '#ffd700',
        backgroundColor: 'rgba(255, 215, 0, 0.1)',
        fill: true,
        tension: 0,
        stepped: OBSERVATION_STEP_MODE,
        borderWidth: 3,
        pointRadius: context => context.raw?.synthetic ? 3 : 0,
        pointHoverRadius: 0,
        order: 0,
    };
    const capacityDataset = capacityDataPoints.length > 0 ? {
        id: 'capacity',
        label: 'Capacity',
        data: displayCapacityDataPoints,
        borderColor: 'rgba(78, 205, 196, 0.84)',
        backgroundColor: 'transparent',
        fill: false,
        tension: 0,
        stepped: OBSERVATION_STEP_MODE,
        borderWidth: 6,
        // Only real capacity changes get a marker; regular and hover radii stay zero.
        pointRadius: context => context.raw?.capacityChanged ? 4 : 0,
        pointHoverRadius: context => context.raw?.capacityChanged ? 4 : 0,
        pointStyle: 'circle',
        pointBackgroundColor: 'hsl(177 65% 68%)',
        pointBorderColor: 'hsl(234 45% 13%)',
        pointBorderWidth: 2,
        // The wide supporting stroke remains visible as a teal halo when the
        // yellow enrollment line is exactly equal to capacity.
        order: 2,
    } : null;
    const historicalDataset = hasHistoricalDataset ? {
        id: 'historical',
        label: historicalComparison.mode === 'professor'
            ? `${historicalComparison.semester}: ${historicalComparison.professorDisplayName}`
            : `${historicalComparison.semester} course aggregate`,
        data: displayHistoricalDataPoints,
        borderColor: 'rgba(247, 249, 252, 0.9)',
        backgroundColor: 'transparent',
        fill: false,
        tension: 0,
        stepped: OBSERVATION_STEP_MODE,
        borderDash: [7, 4],
        borderWidth: 2.5,
        pointRadius: context => (
            context.raw?.observationEnded || context.raw?.removalEnded ? 4 : 0
        ),
        pointHoverRadius: 0,
        pointStyle: context => context.raw?.removalEnded
            ? 'crossRot'
            : context.raw?.observationEnded ? 'triangle' : 'circle',
        pointRotation: context => context.raw?.observationEnded ? 90 : 0,
        pointBackgroundColor: 'rgba(247, 249, 252, 0.15)',
        // Draw above capacity and milestones, but behind the current line.
        order: 1,
    } : null;
    const compactTooltip = window.matchMedia('(max-width: 768px)').matches;
    const coarsePointer = window.matchMedia('(pointer: coarse)').matches;
    canvas.dataset.touchMode = coarsePointer ? 'inspect' : 'hover';
    canvas.dataset.tooltipPinned = 'false';
    const observedXValues = [...displayDataPoints, ...displayHistoricalDataPoints]
        .map(point => point.x)
        .filter(Number.isFinite)
        .sort((first, second) => first - second);
    const observedMinX = observedXValues[0] ?? xBounds.min;
    const observedMaxX = observedXValues.at(-1) ?? xBounds.max;

    appState.chart = new appState.Chart(canvas, {
        type: 'line',
        data: {
            datasets: [historicalDataset, capacityDataset, currentDataset].filter(Boolean),
        },
        options: {
            responsive: true, maintainAspectRatio: false, animation: false,
            onClick: coarsePointer
                ? undefined
                : (event, _elements, chartInstance) => (
                    setChartTooltipPinned(chartInstance, event.x)
                ),
            layout: {
                autoPadding: false,
                padding: { left: 4, right: 4, top: 4, bottom: 4 },
            },
            plugins: {
                annotation: { annotations },
                hoverIndicator: { unmapX, observedMinX, observedMaxX },
                legend: { display: false },
                zoom: {
                    limits: {
                        x: { min: xBounds.min, max: xBounds.max, minRange: xBounds.minRange },
                        y: { min: 0, max: enrollmentScaleMax }
                    },
                    pan: {
                        enabled: !coarsePointer,
                        mode: 'x',
                        threshold: 8,
                        onPan: ({ chart: chartInstance }) => syncChartViewportState(chartInstance, canvas, coarsePointer),
                        onPanComplete: ({ chart: chartInstance }) => syncChartViewportState(chartInstance, canvas, coarsePointer),
                    },
                    zoom: {
                        mode: 'x',
                        wheel: {
                            enabled: true,
                            speed: 0.08
                        },
                        pinch: {
                            enabled: true
                        },
                        drag: {
                            enabled: true,
                            modifierKey: 'shift',
                            threshold: 8,
                            backgroundColor: 'rgba(255, 215, 0, 0.16)',
                            borderColor: '#ffd700',
                            borderWidth: 1
                        },
                        onZoomComplete: ({ chart: chartInstance }) => syncChartViewportState(chartInstance, canvas, coarsePointer),
                    }
                },
                tooltip: {
                    enabled: false,
                    external: context => renderChartReadout(context, canvas, getMilestoneReference),
                    position: 'glide',
                    backgroundColor: '#1a1a2e', titleColor: '#ffd700', bodyColor: '#eaeaea',
                    borderColor: '#3a3a5e', borderWidth: 1,
                    ...(compactTooltip ? {
                        padding: 7,
                        cornerRadius: 6,
                        titleFont: { size: 11, weight: 'bold' },
                        titleMarginBottom: 4,
                        bodyFont: { size: 11 },
                        bodySpacing: 2,
                        boxWidth: 8,
                        boxHeight: 8,
                        boxPadding: 3,
                    } : {}),
                    callbacks: {
                        title: (items) => {
                            if (!items.length) return '';
                            const chartInstance = items[0].chart;
                            const pixelX = chartInstance.$hoverPixelX;
                            const mappedX = Number.isFinite(pixelX)
                                ? chartInstance.scales.x.getValueForPixel(pixelX)
                                : items[0].parsed.x;
                            const timestamp = unmapX(mappedX);
                            if (!Number.isFinite(timestamp)) return '';
                            return new Date(timestamp).toLocaleString('en-US', {
                                month: 'short',
                                day: 'numeric',
                                hour: 'numeric',
                                minute: '2-digit',
                            });
                        },
                        label: (ctx) => {
                            if (ctx.dataset.id === 'historical') {
                                const sourceIndex = displayHistoricalDataPoints[ctx.dataIndex]?.sourceIndex;
                                const historicalPoint = historicalComparison.chartPoints[sourceIndex];
                                if (!historicalPoint) return `${ctx.parsed.y}%`;
                                const openingLevel = Math.round(ctx.parsed.y);
                                const fillLevel = historicalPoint.fill;
                                if (Math.abs(openingLevel - fillLevel) > 1) {
                                    if (compactTooltip) {
                                        return `${historicalComparison.semester}: ${openingLevel}% opening → ${fillLevel}% now`;
                                    }
                                    return [
                                        `${historicalComparison.semester}: ${openingLevel}% of opening capacity`,
                                        `Current capacity: same enrollment would be ${fillLevel}% full`,
                                    ];
                                }
                                return `${historicalComparison.semester}: ${fillLevel}% full`;
                            }
                            const idx = ctx.raw?.sourceIndex ?? ctx.dataIndex;
                            const enrollInfo = visibleChartPoints[idx];
                            if (ctx.dataset.id === 'capacity') {
                                const amount = Number.isFinite(enrollInfo?.capacity)
                                    ? ` ${enrollInfo.capacity}`
                                    : '';
                                const openingCopy = compactTooltip ? 'opening' : 'of opening';
                                return `Capacity: ${amount.trim() || 'N/A'} (${Math.round(ctx.parsed.y)}% ${openingCopy})`;
                            }
                            const count = enrollInfo && enrollInfo.enrollment !== null
                                ? `${enrollInfo.enrollment}/${enrollInfo.capacity}`
                                : 'N/A';
                            const openingLevel = Math.round(ctx.parsed.y);
                            const fillLevel = enrollInfo?.fill ?? openingLevel;
                            const details = Math.abs(openingLevel - fillLevel) > 1
                                ? `${openingLevel}% → ${fillLevel}% full`
                                : `${fillLevel}% full`;
                            return `Enrollment: ${count} (${details})`;
                        }
                    }
                }
            },
            scales: {
                x: {
                    type: 'linear',
                    display: false,
                    min: xBounds.min,
                    max: xBounds.max
                },
                y: {
                    min: 0, max: enrollmentScaleMax,
                    ticks: {
                        display: true,
                        stepSize: 25,
                        color: 'rgba(220, 224, 232, 0.68)',
                        font: { size: 9, weight: '600' },
                        padding: 4,
                        callback: value => `${value}%`,
                    },
                    grid: {
                        // Keep horizontal guides beneath the yellow course series.
                        z: -1,
                        color: 'rgba(220, 224, 232, 0.18)',
                        lineWidth: 1,
                        drawTicks: false,
                    },
                    border: { display: false }
                }
            },
            interaction: { intersect: false, mode: 'glideStepped', axis: 'x' }
        }
    });
    appState.chart.$unmapHoverX = unmapX;
    updateHistoricalLegend();
    syncChartViewportState(appState.chart, canvas, coarsePointer);
}

/**
 * Clear chart active elements (fix for persistent hover on touch).
 */
export function clearChartActiveElements() {
    if (appState.chart) {
        appState.chart.$tooltipPinned = false;
        appState.chart.$pinnedPixelX = null;
        appState.chart.$hoverPixelX = null;
        appState.chart.canvas.dataset.tooltipPinned = 'false';
        appState.chart.setActiveElements([]);
        if (appState.chart.tooltip) {
            appState.chart.tooltip.setActiveElements([], { x: 0, y: 0 });
            appState.chart.tooltip.opacity = 0;
        }
        appState.chart.draw();
    }
    const readout = document.getElementById('chartReadout');
    if (readout) {
        readout.hidden = true;
        readout.textContent = '';
    }
}

if (document.body.dataset.initialCourse) preloadChartJs();

document.querySelectorAll<HTMLElement>('.chart-mode-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
        appState.chartMode = btn.dataset.mode;
        try {
            localStorage.setItem('chartMode', appState.chartMode);
        } catch {
            // Storage can be blocked; the mode still applies for this page.
        }
        setZoomControlsState(false);
        document.querySelectorAll<HTMLElement>('.chart-mode-btn').forEach(b => {
            const isActive = b.dataset.mode === appState.chartMode;
            b.classList.toggle('active', isActive);
            b.setAttribute('aria-pressed', String(isActive));
        });
        if (appState.lastRenderArgs) {
            const a = appState.lastRenderArgs;
            await renderChart(
                a.chartLabel,
                a.chartPoints,
                a.chartDomain,
                {
                    requestVersion: a.requestVersion,
                    historicalComparison: a.historicalComparison,
                },
            );
        }
    });
});

document.getElementById('historicalComparisonToggle')?.addEventListener('click', async () => {
    if (!appState.selectedCourse || appState.historicalComparisonStatus === 'loading') return;
    const course = getHydratedCourse(appState.selectedCourse);
    if (!course) return;
    if (appState.historicalComparisonStatus === 'idle') {
        await resolveHistoricalAvailability(appState.selectedCourse, course, appState.courseRequestVersion);
        if ((appState.historicalComparisonStatus as string) === 'available') {
            await enableHistoricalComparison();
        }
        return;
    }
    if (appState.historicalComparisonStatus === 'failed' && !appState.historicalComparisonDescriptor?.candidate) {
        void resolveHistoricalAvailability(appState.selectedCourse, course, appState.courseRequestVersion);
        return;
    }
    void enableHistoricalComparison();
});

document.getElementById('chartZoomReset')?.addEventListener('click', resetChartZoom);
