import type { PreviewState } from './model';

// This renderer deliberately consumes the dashboard's pure JavaScript chart contract.
// @ts-expect-error The shared browser module intentionally has no TypeScript declaration.
import { buildAverageChartPoints, buildChartPresentation, buildCourseChartDomain, buildSectionActivityTimeline, buildSectionChartPoints, getEnrollmentScaleMax, getSectionTypeName } from '../../../assets/website/src/chartMapping.mjs';

type ChartPoint = {
  snapshotIdx: number;
  timestamp: number;
  enrollmentLevel?: number;
  capacityLevel?: number;
  fill?: number;
  capacityChanged?: boolean;
};

type Milestone = NonNullable<PreviewState['milestones']>[number];

function escapeHtml(value: unknown): string {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function compactCourse(state: PreviewState) {
  const source = state.course;
  if (!source) return null;
  const sectionCodes = [...new Set([
    ...Object.keys(source.sections ?? {}),
    ...Object.keys(source.sectionHistory ?? {}),
  ])].sort();
  return {
    ah: (source.averageHistory ?? []).map((point) => ({
      i: point.timestampIdx,
      f: point.fill,
    })),
    s: Object.fromEntries(sectionCodes.map((code) => {
      const section = source.sections?.[code];
      return [code, {
        t: getSectionTypeName(section?.type),
        h: (source.sectionHistory?.[code] ?? []).map((point) => ({
          i: point.timestampIdx,
          f: point.fill,
          e: point.enrollment,
          c: point.capacity,
        })),
      }];
    })),
    ev: source.events ?? [],
  };
}

function stepPath(
  xValues: number[],
  yValues: number[],
  xFor: (value: number) => number,
  yFor: (value: number) => number,
): string {
  const points = xValues.flatMap((x, index) => (
    Number.isFinite(x) && Number.isFinite(yValues[index])
      ? [{ x: xFor(x), y: yFor(yValues[index]) }]
      : []
  ));
  if (points.length === 0) return '';
  return points.slice(1).reduce(
    (path, point) => `${path} H ${point.x.toFixed(1)} V ${point.y.toFixed(1)}`,
    `M ${points[0].x.toFixed(1)} ${points[0].y.toFixed(1)}`,
  );
}

function graph(state: PreviewState): string {
  const course = compactCourse(state);
  const snapshots = (state.timestamps ?? []).map((ts) => ({ ts }));
  if (!course || snapshots.length === 0) return '';

  const sectionEntries = Object.entries(course.s);
  let points: ChartPoint[];
  let domain: ChartPoint[];
  let seriesLabel: string;
  let activeIntervals: Array<{ start: number; end: number }> = [];
  if (sectionEntries.length === 1) {
    const [sectionCode, section] = sectionEntries[0];
    points = buildSectionChartPoints(section, snapshots);
    domain = buildCourseChartDomain(course, snapshots);
    seriesLabel = `${section.t} section ${sectionCode}`;
    activeIntervals = buildSectionActivityTimeline(
      sectionCode, section, course.ev, snapshots, course,
    ).intervals;
  } else {
    points = buildAverageChartPoints(course, snapshots);
    domain = buildCourseChartDomain(course, snapshots);
    const types = [...new Set(sectionEntries.map(([, section]) => section.t))];
    const typeLabel = types.length === 1
      ? types[0]
      : `${types.slice(0, -1).join(', ')} and ${types.at(-1)}`;
    seriesLabel = sectionEntries.length > 1
      ? `${typeLabel}, ${sectionEntries.length} sections grouped`
      : 'Course average';
  }
  if (points.length < 2) return '';

  const visibleMilestones: Milestone[] = state.milestones ?? [];
  const presentation = buildChartPresentation({
    points,
    domain,
    milestones: visibleMilestones,
    phaseMilestones: state.milestones ?? [],
    mode: 'phased',
  });
  const mappedDomain = presentation.domainXValues.length > 0
    ? presentation.domainXValues
    : presentation.xValues;
  const mappedMilestones = visibleMilestones
    .map((milestone) => presentation.mapTime(Date.parse(milestone.time.replace(' ', 'T'))))
    .filter(Number.isFinite);
  const xDomain = [...mappedDomain, ...mappedMilestones];
  const xMin = Math.min(...xDomain);
  const xMax = Math.max(...xDomain);
  const xFor = (value: number) => 40 + ((value - xMin) / Math.max(xMax - xMin, 1)) * 1040;
  const yMax = getEnrollmentScaleMax([
    ...presentation.enrollmentValues,
    ...presentation.capacityValues,
  ]);
  const yFor = (value: number) => 190 - (value / yMax) * 150;
  const intervalIndices = (activeIntervals.length > 0 ? activeIntervals : [{
    start: Number.NEGATIVE_INFINITY,
    end: Number.POSITIVE_INFINITY,
  }]).map(({ start, end }) => presentation.visiblePoints.flatMap(
    (point: ChartPoint, index: number) => (
      point.snapshotIdx >= start && point.snapshotIdx < end ? [index] : []
    ),
  )).filter((indices: number[]) => indices.length > 0);
  const pathsFor = (values: number[], className: string) => intervalIndices.map(
    (indices: number[]) => `<path class="${className}" d="${stepPath(
      indices.map((index) => presentation.xValues[index]),
      indices.map((index) => values[index]),
      xFor,
      yFor,
    )}"/>`,
  ).join('');
  const enrollmentPaths = pathsFor(presentation.enrollmentValues, 'enrollment-series');
  const capacityPaths = pathsFor(presentation.capacityValues, 'capacity-series');

  const milestones = visibleMilestones.flatMap((milestone, index) => {
    const parsed = Date.parse(milestone.time.replace(' ', 'T'));
    if (!Number.isFinite(parsed)) return [];
    const x = xFor(presentation.mapTime(parsed));
    const previousPriority = index > 0 ? visibleMilestones[index - 1]?.priority : null;
    const label = milestone.priority && milestone.priority !== previousPriority
      ? `P${milestone.priority} · ${milestone.label}`
      : milestone.label;
    const color = milestone.priority ? milestone.color : '#94A3B8';
    const textX = index === 0 ? x + 6 : index === visibleMilestones.length - 1 ? x - 6 : x;
    const anchor = index === 0 ? 'start' : index === visibleMilestones.length - 1 ? 'end' : 'middle';
    return `<g class="milestone"><line x1="${x.toFixed(1)}" y1="38" x2="${x.toFixed(1)}" y2="190" style="stroke:${escapeHtml(color)}"/><text x="${textX.toFixed(1)}" y="22" text-anchor="${anchor}">${escapeHtml(label)}</text></g>`;
  }).join('');
  const latestX = presentation.xValues.at(-1);
  const latestMarker = Number.isFinite(latestX)
    ? `<circle class="latest-marker" cx="${xFor(latestX).toFixed(1)}" cy="${yFor(presentation.enrollmentValues.at(-1) ?? 0).toFixed(1)}" r="6"><title>Latest observation</title></circle>`
    : '';
  const capacityChange = presentation.visiblePoints.findIndex(
    (point: ChartPoint) => point.capacityChanged,
  );
  const annotation = capacityChange > 0
    ? `<text class="capacity-change" x="${xFor(presentation.xValues[capacityChange]).toFixed(1)}" y="57">CAPACITY CHANGED</text>`
    : '';

  return `<svg class="graph" viewBox="0 0 1120 210" role="img" aria-label="Equally spaced milestone enrollment and capacity history" data-segments="${intervalIndices.length}"><line class="axis" x1="40" y1="190" x2="1080" y2="190"/>${milestones}${annotation}${capacityPaths}${enrollmentPaths}${latestMarker}<text class="series-label" x="40" y="207">${escapeHtml(seriesLabel)}, milestone view</text></svg>`;
}

function formatUpdated(value: string | undefined): string {
  if (!value) return 'recently';
  const parsed = new Date(value.replace(' ', 'T'));
  if (!Number.isFinite(parsed.getTime())) return value;
  return new Intl.DateTimeFormat('en-GB', {
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone: 'Asia/Almaty',
  }).format(parsed).replace(',', '') + ' Astana time';
}

function formatNextMilestone(next: { label: string; time: string; priority?: string } | null | undefined): string {
  if (!next) return '';
  const parsed = new Date(String(next.time).replace(' ', 'T'));
  if (!Number.isFinite(parsed.getTime())) return '';
  const when = new Intl.DateTimeFormat('en-GB', {
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone: 'Asia/Almaty',
  }).format(parsed).replace(',', '');
  const priority = next.priority ? `P${next.priority} · ` : '';
  return `Next: ${priority}${String(next.label)} · ${when}`;
}

function formatCurrentMilestone(
  current: { label: string; time: string; priority?: string } | null | undefined,
): string {
  if (!current?.label) return '';
  const priority = current.priority ? `P${current.priority} · ` : '';
  return `Open now: ${priority}${current.label}`;
}

function formatFinalState(value: string | undefined): string {
  if (!value) return 'Final state';
  return `Final: ${formatUpdated(value)}`;
}

function courseSectionSummary(state: PreviewState): string {
  const sectionEntries = Object.entries(state.course?.sections ?? {});
  if (sectionEntries.length === 0) return '';
  const types = [...new Set(sectionEntries.map(([, section]) => getSectionTypeName(section.type)))];
  const availabilityTypes = state.availability?.types ?? [];
  const sectionCount = availabilityTypes.reduce(
    (total, item) => total + (item.sectionCount ?? 0),
    0,
  ) || sectionEntries.length;
  const openSections = availabilityTypes.reduce(
    (total, item) => total + (item.openSections ?? 0),
    0,
  );
  const fullSections = Math.max(sectionCount - openSections, 0);
  if (sectionCount === 1) {
    const stateLabel = availabilityTypes.length > 0 ? ` · ${openSections === 1 ? 'open' : 'full'}` : '';
    return `1 ${types[0]} section${stateLabel}`;
  }
  if (availabilityTypes.length > 0) {
    return `${sectionCount} sections · ${openSections} open / ${fullSections} full`;
  }
  return `${sectionCount} sections · ${types.join(' + ')}`;
}

function courseReadout(state: PreviewState, includeBreakdown = false): Array<[string, string]> {
  const availability = state.availability;
  if (!availability) return [];
  const label = availability.kind === 'registration-places' ? 'places' : 'seats';
  const lines: Array<[string, string]> = [
    ['Availability', `${availability.available} ${label} open`],
  ];
  if (includeBreakdown && (availability.types?.length ?? 0) > 1) {
    for (const item of availability.types ?? []) {
      const percent = item.capacity > 0
        ? `${Math.round((item.enrollment / item.capacity) * 100)}% full`
        : 'No capacity';
      const sectionState = Number.isInteger(item.openSections) && Number.isInteger(item.sectionCount)
        ? ` · ${item.openSections}/${item.sectionCount} sections open`
        : '';
      lines.push([item.type, `${item.enrollment}/${item.capacity} · ${percent}${sectionState}`]);
    }
    return lines;
  }
  const totals = availability.kind === 'registration-places'
    ? [...(availability.types ?? [])].sort((first, second) => (
      (first.available ?? Math.max(first.capacity - first.enrollment, 0))
        - (second.available ?? Math.max(second.capacity - second.enrollment, 0))
      || first.type.localeCompare(second.type)
    ))[0]
    : availability.types?.[0];
  if (totals) {
    const percent = totals.capacity > 0 ? ` (${Math.round((totals.enrollment / totals.capacity) * 100)}% full)` : '';
    const enrollmentLabel = availability.kind === 'registration-places'
      ? `${totals.type} enrollment`
      : 'Enrollment';
    lines.push([enrollmentLabel, `${totals.enrollment}/${totals.capacity}${percent}`]);
  } else if (availability.breakdown) {
    lines.push(['Sections', availability.breakdown]);
  }
  return lines;
}

export function renderCard(state: PreviewState): string {
  const course = state.kind === 'course';
  const status = state.status ?? 'current';
  const eyebrow = course
    ? status === 'removed'
      ? state.semester
      : `${state.semester} · REGISTRATION ${status === 'archived' ? 'CLOSED' : 'OPEN'}`
    : 'Enrollment Monitor';
  const title = course ? state.code : state.semester;
  const subtitle = course ? state.title : `${state.courseCount} courses, ${state.sectionCount} sections`;
  const currentMilestone = formatCurrentMilestone(state.priority?.current);
  const priorityCopy = currentMilestone || state.priority?.label || '';
  const priority = priorityCopy ? `<span class="priority">${escapeHtml(priorityCopy)}</span>` : '';
  const nextMilestone = formatNextMilestone(state.priority?.next);
  const context = nextMilestone || (status === 'archived' || status === 'removed'
    ? formatFinalState(state.lastChanged ?? state.updated)
    : '');
  const next = context ? `<span class="course-readout-context">${escapeHtml(context)}</span>` : '';
  const chart = graph(state);
  const sectionSummary = courseSectionSummary(state);
  const sections = sectionSummary ? `<span class="course-readout-sections">${escapeHtml(sectionSummary)}</span>` : '';
  const readout = courseReadout(state, !chart).map(([label, value]) => (
    `<span class="course-readout-line"><span class="course-readout-label">${escapeHtml(label)}</span><span class="course-readout-value">${escapeHtml(value)}</span></span>`
  )).join('');
  const courseReadoutMarkup = priority || next || sections || readout
    ? `<div class="course-readout"><div class="course-readout-header"><span class="course-readout-registration">${priority}${next}</span>${sections}</div><div class="course-readout-values">${readout}</div></div>`
    : '';
  const eligible = state.priority?.eligible?.length
    ? `<span class="semester-eligible">Eligible: ${escapeHtml(state.priority.eligible.join(', '))}</span>`
    : '';
  const semesterRegistration = priority || nextMilestone || eligible
    ? `<div class="semester-registration">${priority}${eligible}${next}</div>`
    : '';
  const details = course
    ? `<div class="subtitle">${escapeHtml(subtitle)}</div>${courseReadoutMarkup}`
    : `<div class="semester-stats">
        <div><strong>${state.courseCount ?? 0}</strong><span>courses</span></div>
        <div><strong>${state.sectionCount ?? 0}</strong><span>sections</span></div>
        <div><strong>${state.fullSectionCount ?? 0}</strong><span>full sections</span></div>
        <div><strong>${state.openSeats ?? 0}</strong><span>seats open</span></div>
      </div><div class="semester-update">${state.archived ? 'Registration closed · final update' : `Updated ${formatUpdated(state.updated)}`}</div>${semesterRegistration}`;
  return `<!doctype html><html><head><meta charset="utf-8"><style>
*{box-sizing:border-box}html,body{margin:0;width:1200px;height:630px;overflow:hidden;background:hsl(234 45% 13%);color:hsl(0 0% 94%);font-family:'JetBrains Mono','Fira Code','SF Mono',monospace;font-variant-numeric:tabular-nums}.card{position:relative;width:100%;height:100%;padding:42px 64px;background:linear-gradient(135deg,hsl(220 45% 16%),hsl(234 45% 13%) 72%)}.mark{position:absolute;top:44px;right:64px;width:30px;height:30px;background:#ff5722}.eyebrow{color:hsl(48 100% 55%);font-size:22px;line-height:1.25;letter-spacing:.09em;text-transform:uppercase}.title{margin:14px 0 4px;max-width:980px;font-size:62px;line-height:1.04;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.subtitle{max-width:980px;color:hsl(0 0% 84%);font-size:27px;line-height:1.2;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.priority{color:hsl(48 100% 55%);font-size:17px;font-weight:700;line-height:1.25}.course-readout{width:100%;margin-top:12px;padding:9px 13px;border:1px solid hsl(220 12% 62%);background:hsl(234 45% 13% / .8);box-shadow:0 3px 10px hsl(234 45% 8% / .24);font-size:17px;line-height:1.25}.course-readout-header{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:5px 28px;min-height:21px}.course-readout-registration{display:flex;flex-wrap:wrap;align-items:center;gap:5px 28px}.course-readout-context{color:hsl(48 100% 55%);font-size:17px;font-weight:700}.course-readout-sections{color:hsl(0 0% 84%);font-size:15px}.course-readout-values{display:flex;flex-wrap:wrap;gap:4px 24px;margin-top:3px}.course-readout-line{display:inline-flex;gap:7px;white-space:nowrap}.course-readout-label{color:hsl(0 0% 82%)}.course-readout-value{font-weight:600}.course-readout-line:last-child .course-readout-value{color:hsl(48 100% 55%)}.has-graph .graph{position:absolute;left:28px;bottom:18px;width:1144px;height:294px}.no-graph.course-card{display:flex;flex-direction:column;justify-content:center;padding-top:48px;padding-bottom:48px}.no-graph.course-card .mark{top:66px}.no-graph.course-card .title{margin-top:18px;font-size:72px}.no-graph.course-card .subtitle{font-size:31px;margin-top:7px}.no-graph.course-card .course-readout{margin-top:25px;min-height:230px;padding:22px 26px;display:flex;flex-direction:column;justify-content:center;font-size:22px}.no-graph.course-card .course-readout-header{gap:12px 40px}.no-graph.course-card .course-readout-registration{gap:12px 40px}.no-graph.course-card .priority,.no-graph.course-card .course-readout-context{font-size:21px}.no-graph.course-card .course-readout-sections{font-size:19px}.no-graph.course-card .course-readout-values{display:flex;flex-direction:column;gap:10px;margin-top:16px}.no-graph.course-card .course-readout-line{display:grid;grid-template-columns:160px minmax(0,1fr);gap:14px;white-space:normal}.no-graph.course-card .course-readout-value{text-align:right}.semester-card{padding:62px 64px}.semester-card .mark{top:64px}.semester-card .title{margin-top:26px;font-size:88px}.semester-stats{display:grid;grid-template-columns:repeat(4,1fr);margin-top:54px;border-top:1px solid hsl(240 4% 50%);border-bottom:1px solid hsl(240 4% 50%)}.semester-stats div{padding:34px 22px;text-align:right}.semester-stats div+div{border-left:1px solid hsl(240 4% 50%)}.semester-stats strong,.semester-stats span{display:block}.semester-stats strong{font-size:46px;line-height:1.1;color:hsl(0 0% 94%)}.semester-stats span{margin-top:10px;color:hsl(0 0% 82%);font-size:17px;text-transform:uppercase;letter-spacing:.05em}.semester-update{margin-top:32px;font-size:24px;line-height:1.25}.semester-registration{display:flex;flex-wrap:wrap;gap:10px 32px;align-items:center;margin-top:20px;padding:18px 22px;border:1px solid hsl(220 12% 62%);background:hsl(234 45% 13% / .62)}.semester-registration .priority,.semester-registration .course-readout-context{font-size:21px}.semester-eligible{color:hsl(0 0% 84%);font-size:20px}.graph .axis{stroke:hsl(220 12% 62%);stroke-width:1.25}.enrollment-series{fill:none;stroke:hsl(48 100% 55%);stroke-width:5;stroke-linecap:round;stroke-linejoin:round}.capacity-series{fill:none;stroke:hsl(177 65% 68%);stroke-width:3;stroke-dasharray:9 7;opacity:1}.milestone line{stroke-width:2;stroke-dasharray:5 5;opacity:1}.milestone text,.series-label,.capacity-change{font:12px 'JetBrains Mono','Fira Code','SF Mono',monospace;letter-spacing:.04em}.milestone text{fill:hsl(0 0% 96%);font-weight:700;paint-order:stroke;stroke:hsl(234 45% 13%);stroke-width:4px;stroke-linejoin:round}.series-label{fill:hsl(0 0% 88%)}.capacity-change{fill:hsl(177 65% 78%)}.latest-marker{fill:hsl(48 100% 55%);stroke:hsl(234 45% 13%);stroke-width:3}.removed{display:inline-block;margin-left:14px;padding:5px 8px;background:hsl(346 100% 60%);color:hsl(234 45% 13%);font-size:16px;line-height:1.25}
</style></head><body><main class="card ${course ? 'course-card' : 'semester-card'} ${chart ? 'has-graph' : 'no-graph'}"><span class="mark"></span><div class="eyebrow">${escapeHtml(eyebrow)}${state.status === 'removed' ? '<span class="removed">REMOVED</span>' : ''}</div><h1 class="title">${escapeHtml(title)}</h1>${details}${chart}</main></body></html>`;
}
