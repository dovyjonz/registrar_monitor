import { describe, expect, it } from 'vitest';

import { renderCard } from '../src/card';

describe('preview card', () => {
  it('keeps full and over-capacity graph points in the rendered series', () => {
    const html = renderCard({
      schemaVersion: 1,
      kind: 'course',
      hash: 'a1b2c3d4e5f6',
      semester: 'Fall 2026',
      semesterSlug: 'fall-2026',
      slug: 'ant-140',
      code: 'ANT 140',
      title: 'Introduction',
      timestamps: ['2026-08-01T09:00:00+05:00', '2026-08-02T09:00:00+05:00'],
      course: { averageHistory: [
        { timestampIdx: 0, fill: 1 },
        { timestampIdx: 1, fill: 1.1 },
      ], sections: {}, sectionHistory: {} },
    });

    expect(html).toContain('class="enrollment-series"');
    expect(html).toContain('class="capacity-series"');
    expect(html).toContain("'JetBrains Mono','Fira Code','SF Mono',monospace");
    expect(html).toContain('hsl(234 45% 13%)');
    expect(html).not.toContain('spooktaken');
  });

  it('shows the removed status once as a prominent badge', () => {
    const html = renderCard({
      schemaVersion: 1,
      kind: 'course',
      hash: 'a1b2c3d4e5f6',
      semester: 'Spring 2026',
      semesterSlug: 'spring-2026',
      slug: 'ant-214',
      code: 'ANT 214',
      status: 'removed',
    });

    expect(html).toContain('<div class="eyebrow">Spring 2026<span class="removed">REMOVED</span>');
    expect(html).not.toContain('Spring 2026 — removed');
  });

  it('labels current and archived courses by registration state', () => {
    const current = renderCard({
      schemaVersion: 1,
      kind: 'course',
      hash: 'a1b2c3d4e5f6',
      semester: 'Fall 2026',
      semesterSlug: 'fall-2026',
      status: 'current',
    });
    const archived = renderCard({
      schemaVersion: 1,
      kind: 'course',
      hash: 'b1b2c3d4e5f6',
      semester: 'Spring 2026',
      semesterSlug: 'spring-2026',
      status: 'archived',
      archived: true,
    });

    expect(current).toContain('Fall 2026 · REGISTRATION OPEN');
    expect(archived).toContain('Spring 2026 · REGISTRATION CLOSED');
    expect(archived).not.toContain('ARCHIVED');
  });

  it('splits availability and priority into concise scan lines', () => {
    const html = renderCard({
      schemaVersion: 1,
      kind: 'course',
      hash: 'a1b2c3d4e5f6',
      semester: 'Spring 2026',
      semesterSlug: 'spring-2026',
      availability: {
        sentence: '7 registration places available. Limited by lab and lecture.',
        breakdown: 'Labs 1/2 open, Lecture 1/1 open.',
        available: 7,
        kind: 'registration-places',
        limitingTypes: ['Lab', 'Lecture'],
        types: [
          { type: 'Lecture', enrollment: 50, capacity: 60, available: 10 },
          { type: 'Lab', enrollment: 45, capacity: 50, available: 5 },
        ],
      },
      priority: { label: 'PRIORITY 3 · ALL' },
    });

    expect(html).toContain('<span class="course-readout-label">Availability</span><span class="course-readout-value">7 places open</span>');
    expect(html).not.toContain('course-readout-label">Limit');
    expect(html).not.toContain('Lab + Lecture');
    expect(html).toContain('<span class="course-readout-label">Lab</span><span class="course-readout-value">45/50 · 90% full</span>');
    expect(html).toContain('<span class="priority">PRIORITY 3 · ALL</span>');
    expect(html).not.toContain('7 registration places available');
  });

  it('uses the readout width for the next milestone', () => {
    const html = renderCard({
      schemaVersion: 1,
      kind: 'course',
      hash: 'a1b2c3d4e5f6',
      semester: 'Summer 2026',
      semesterSlug: 'summer-2026',
      availability: {
        sentence: '16 seats open; 34/50 enrolled.',
        breakdown: 'Lecture 1/1 open.',
        available: 16,
        kind: 'seats',
        types: [{
          type: 'Lecture',
          enrollment: 34,
          capacity: 50,
          openSections: 1,
          sectionCount: 1,
        }],
      },
      priority: {
        label: 'PRIORITY 1',
        current: { label: 'Y2', priority: '1', time: '2026-05-12T11:00:00+05:00' },
        next: { label: 'Y1', priority: '1', time: '2026-05-12T13:00:00+05:00' },
      },
      course: {
        sections: { '1L': { type: 'L' } },
        sectionHistory: {},
      },
    });

    expect(html).toContain('Open now: P1 · Y2');
    expect(html).toContain('Next: P1 · Y1 · 12 May 13:00');
    expect(html).toContain('.course-readout{width:100%');
    expect(html).toContain('justify-content:space-between;gap:5px 28px');
    expect(html).toContain('flex-wrap:wrap');
    expect(html).toContain('class="course-readout-registration"><span class="priority">Open now: P1 · Y2</span><span class="course-readout-context">Next: P1 · Y1 · 12 May 13:00</span>');
    expect(html).toContain('<span class="course-readout-sections">1 Lecture section · open</span>');
    expect(html).toContain('.course-readout-context{color:hsl(48 100% 55%);font-size:17px;font-weight:700');
  });

  it('summarizes mixed open and full sections', () => {
    const html = renderCard({
      schemaVersion: 1,
      kind: 'course',
      hash: 'a1b2c3d4e5f6',
      semester: 'Fall 2026',
      semesterSlug: 'fall-2026',
      availability: {
        sentence: '3 registration places available.',
        breakdown: 'Lectures 2/3 open, Labs 3/5 open.',
        available: 3,
        kind: 'registration-places',
        limitingTypes: ['Lecture'],
        types: [
          { type: 'Lecture', enrollment: 50, capacity: 60, available: 3, openSections: 2, sectionCount: 3 },
          { type: 'Lab', enrollment: 45, capacity: 50, available: 5, openSections: 3, sectionCount: 5 },
        ],
      },
      course: {
        sections: Object.fromEntries([
          ...[1, 2, 3].map((index) => [`${index}L`, { type: 'L' }]),
          ...[1, 2, 3, 4, 5].map((index) => [`${index}B`, { type: 'B' }]),
        ]),
        sectionHistory: {},
      },
    });

    expect(html).toContain('8 sections · 5 open / 3 full');
    expect(html).toContain('<span class="course-readout-label">Lecture</span><span class="course-readout-value">50/60 · 83% full · 2/3 sections open</span>');
  });

  it('renders every historical milestone guide with equal importance and spacing', () => {
    const html = renderCard({
      schemaVersion: 1,
      kind: 'course',
      hash: 'a1b2c3d4e5f6',
      semester: 'Summer 2026',
      semesterSlug: 'summer-2026',
      timestamps: [
        '2026-05-10T10:00:00+05:00',
        '2026-05-20T10:00:00+05:00',
      ],
      milestones: [
        { time: '2026-05-12T10:00:00+05:00', label: 'Y4+', color: '#FF1744', priority: '1' },
        { time: '2026-05-13T10:00:00+05:00', label: 'Y3', color: '#FF1744', priority: '1' },
        { time: '2026-05-14T10:00:00+05:00', label: 'Y2', color: '#FF1744', priority: '1' },
        { time: '2026-05-15T10:00:00+05:00', label: 'Y1', color: '#FF1744', priority: '1' },
        { time: '2026-05-16T10:00:00+05:00', label: 'Y2', color: '#00BCD4', priority: '2' },
        { time: '2026-05-17T10:00:00+05:00', label: 'ALL', color: '#D500F9', priority: '3' },
        { time: '2026-05-18T10:00:00+05:00', label: 'Drop', color: '#78909C' },
        { time: '2026-05-19T10:00:00+05:00', label: 'Close', color: '#78909C' },
      ],
      course: { averageHistory: [
        { timestampIdx: 0, fill: 0.2 },
        { timestampIdx: 1, fill: 0.8 },
      ], sections: {}, sectionHistory: {} },
    });

    expect(html).toContain('Equally spaced milestone enrollment and capacity history');
    expect(html).toContain('class="milestone"');
    expect(html).toContain('P1 · Y4+');
    expect(html).toContain('P2 · Y2');
    expect(html).toContain('P3 · ALL');
    expect(html).toContain('>Y3</text>');
    expect(html).toContain('>Drop</text>');
    expect(html).toContain('>Close</text>');
    expect(html).toContain('milestone view');
    const positions = [...html.matchAll(/class="milestone"><line x1="([\d.]+)"/g)]
      .map((match) => Number(match[1]));
    expect(positions).toHaveLength(8);
    const gaps = positions.slice(1).map((position, index) => position - positions[index]);
    expect(Math.max(...gaps) - Math.min(...gaps)).toBeLessThan(0.2);
    expect(html).not.toContain('minor-milestone');
    const labelBaselines = [...html.matchAll(/<text x="[\d.]+" y="([\d.]+)" text-anchor=/g)]
      .map((match) => Number(match[1]));
    expect(new Set(labelBaselines)).toEqual(new Set([22]));
    expect(html).toContain('style="stroke:#94A3B8"');
    expect(html).toContain('class="latest-marker"');
  });

  it('ends the observed line before the next future milestone', () => {
    const html = renderCard({
      schemaVersion: 1,
      kind: 'course',
      hash: 'a1b2c3d4e5f6',
      semester: 'Fall 2026',
      semesterSlug: 'fall-2026',
      timestamps: ['2026-08-13T09:00:00+05:00', '2026-08-13T10:55:00+05:00'],
      milestones: [
        { time: '2026-08-13T09:00:00+05:00', label: 'Y4+', color: '#00BCD4', priority: '2' },
        { time: '2026-08-13T11:00:00+05:00', label: 'Y3', color: '#00BCD4', priority: '2' },
        { time: '2026-08-13T13:00:00+05:00', label: 'Y2', color: '#00BCD4', priority: '2' },
      ],
      course: {
        averageHistory: [
          { timestampIdx: 0, fill: 0.5 },
          { timestampIdx: 1, fill: 0.75 },
        ],
        sections: {},
        sectionHistory: {},
      },
    });

    const path = html.match(/class="enrollment-series" d="([^"]+)"/)?.[1] ?? '';
    const lineEnd = Number([...path.matchAll(/H ([\d.]+)/g)].at(-1)?.[1]);
    const milestonePositions = [...html.matchAll(/class="milestone"><line x1="([\d.]+)"/g)]
      .map((match) => Number(match[1]));
    expect(lineEnd).toBeLessThan(milestonePositions[1]);
  });

  it('renders observed capacity as a dashed step series with one change annotation', () => {
    const html = renderCard({
      schemaVersion: 1,
      kind: 'course',
      hash: 'a1b2c3d4e5f6',
      semester: 'Fall 2026',
      semesterSlug: 'fall-2026',
      timestamps: ['2026-08-01T09:00:00+05:00', '2026-08-02T09:00:00+05:00'],
      course: {
        averageHistory: [
          { timestampIdx: 0, fill: 0.5 },
          { timestampIdx: 1, fill: 0.5 },
        ],
        sections: { '1L': { type: 'Lecture' } },
        sectionHistory: { '1L': [
          { timestampIdx: 0, fill: 0.5, enrollment: 10, capacity: 20 },
          { timestampIdx: 1, fill: 1, enrollment: 10, capacity: 10 },
        ] },
        events: [],
      },
    });

    expect(html).toContain('class="capacity-series"');
    expect(html).toContain('stroke-dasharray:9 7');
    expect(html).toContain('CAPACITY CHANGED');
    expect(html).toContain('Lecture section 1L, milestone view');
  });

  it('ends a removed section segment and starts a new one after its return', () => {
    const html = renderCard({
      schemaVersion: 1,
      kind: 'course',
      hash: 'a1b2c3d4e5f6',
      semester: 'Fall 2026',
      semesterSlug: 'fall-2026',
      timestamps: [0, 1, 2, 3].map((day) => `2026-08-0${day + 1}T09:00:00+05:00`),
      course: {
        averageHistory: [],
        sections: { '1L': { type: 'Lecture' } },
        sectionHistory: { '1L': [0, 1, 2, 3].map((timestampIdx) => ({
          timestampIdx,
          fill: 0.5,
          enrollment: 10,
          capacity: 20,
        })) },
        events: [
          { eventType: 'section_removed', sectionCode: '1L', timestampIdx: 2 },
          { eventType: 'section_added', sectionCode: '1L', timestampIdx: 3 },
        ],
      },
    });

    expect(html).toContain('data-segments="2"');
    expect(html.match(/class="enrollment-series"/g)).toHaveLength(2);
  });

  it('expands useful content instead of reserving an empty graph region', () => {
    const html = renderCard({
      schemaVersion: 1,
      kind: 'course',
      hash: 'a1b2c3d4e5f6',
      semester: 'Fall 2026',
      semesterSlug: 'fall-2026',
      slug: 'wcs-210-asc-200',
      code: 'WCS 210 / ASC 200',
      title: 'Academic Writing, Science Communication, and Media Literacy',
      status: 'current',
      availability: {
        sentence: '2 seats open.',
        breakdown: 'Seminars 1/2 open.',
        available: 2,
        kind: 'seats',
        types: [{
          type: 'Seminar', enrollment: 58, capacity: 60, available: 2,
          openSections: 1, sectionCount: 2,
        }],
      },
      timestamps: ['2026-08-02T18:49:38+05:00'],
      course: {
        sections: { '1S': { type: 'S' }, '2S': { type: 'S' } },
        averageHistory: [{ timestampIdx: 0, fill: 0.97 }],
        sectionHistory: {},
      },
    });

    expect(html).toContain('course-card no-graph');
    expect(html).toContain('.no-graph.course-card .course-readout{margin-top:25px;min-height:230px');
    expect(html).not.toContain('<svg class="graph"');
    expect(html).toContain('2 sections · 1 open / 1 full');
    expect(html).toContain('<span class="course-readout-label">Enrollment</span><span class="course-readout-value">58/60 (97% full)</span>');
    expect(html).toContain('.no-graph.course-card .course-readout-value{text-align:right}');
  });

  it('uses the published final timestamp on archived and removed cards', () => {
    const html = renderCard({
      schemaVersion: 1,
      kind: 'course',
      hash: 'a1b2c3d4e5f6',
      semester: 'Spring 2026',
      semesterSlug: 'spring-2026',
      slug: 'ant-214',
      code: 'ANT 214 / SOC 214',
      title: 'Social Theory',
      status: 'removed',
      archived: true,
      lastChanged: '2026-01-31T22:36:19+05:00',
    });

    expect(html).toContain('Final: 31 Jan 22:36 Astana time');
    expect(html).toContain('course-card no-graph');
  });

  it('fills semester cards with a larger count grid', () => {
    const html = renderCard({
      schemaVersion: 1,
      kind: 'semester',
      hash: 'a1b2c3d4e5f6',
      semester: 'Fall 2026',
      semesterSlug: 'fall-2026',
      courseCount: 404,
      sectionCount: 884,
      fullSectionCount: 127,
      openSeats: 2318,
      updated: '2026-08-03T15:45:00+05:00',
      priority: {
        label: 'PRIORITY 2',
        eligible: ['Y4+', 'Y3'],
        next: { label: 'Y2', time: '2026-08-04T13:00:00+05:00' },
      },
    });

    expect(html).toContain('semester-card no-graph');
    expect(html).toContain('.semester-card .title{margin-top:26px;font-size:88px}');
    expect(html).toContain('font-variant-numeric:tabular-nums');
    expect(html).toContain('.semester-stats div{padding:34px 22px;text-align:right}');
    expect(html).toContain('Eligible: Y4+, Y3');
    expect(html).toContain('Next: Y2 · 4 Aug 13:00');
  });
});
