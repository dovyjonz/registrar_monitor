export type PreviewKind = 'semester' | 'course';

export type RouteIdentity = {
  kind: PreviewKind;
  semesterSlug: string;
  slug?: string;
  hash: string;
};

export type PreviewState = {
  schemaVersion: number;
  kind: PreviewKind;
  hash: string;
  semester: string;
  semesterSlug: string;
  slug?: string;
  code?: string;
  title?: string;
  status?: string;
  availability?: {
    sentence: string;
    breakdown: string;
    available: number;
    kind?: 'seats' | 'registration-places';
    limitingTypes?: string[];
    types?: Array<{
      type: string;
      enrollment: number;
      capacity: number;
      available?: number;
      openSections?: number;
      sectionCount?: number;
    }>;
  };
  courseCount?: number;
  sectionCount?: number;
  fullSectionCount?: number;
  openSeats?: number;
  updated?: string;
  lastChanged?: string;
  archived?: boolean;
  priority?: {
    label: string;
    eligible?: string[];
    next?: { label: string; time: string } | null;
  } | null;
  milestones?: Array<{
    time: string;
    label: string;
    color: string;
    priority?: string;
  }>;
  timestamps?: string[];
  course?: {
    averageHistory?: Array<{ timestampIdx: number; fill: number }>;
    sections?: Record<string, { type?: string }>;
    sectionHistory?: Record<string, Array<{
      timestampIdx: number;
      fill: number;
      enrollment: number;
      capacity: number;
    }>>;
    events?: Array<{
      eventType?: string;
      sectionCode?: string;
      timestampIdx?: number;
    }>;
  };
};

const SEMESTER = /^(fall|spring|summer)-\d{4}$/;
const SLUG = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const HASH = /^[a-f0-9]{12}$/;

export function parsePreviewRoute(pathname: string): RouteIdentity | null {
  const parts = pathname.split('/').filter(Boolean);
  if (parts[0] !== 'preview') return null;
  if (parts[1] === 'semester' && parts.length === 4) {
    const hash = parts[3].replace(/\.png$/, '');
    if (!parts[3].endsWith('.png') || !SEMESTER.test(parts[2]) || !HASH.test(hash)) {
      return null;
    }
    return { kind: 'semester', semesterSlug: parts[2], hash };
  }
  if (parts[1] === 'course' && parts.length === 5) {
    const hash = parts[4].replace(/\.png$/, '');
    if (
      !parts[4].endsWith('.png')
      || !SEMESTER.test(parts[2])
      || !SLUG.test(parts[3])
      || !HASH.test(hash)
    ) return null;
    return { kind: 'course', semesterSlug: parts[2], slug: parts[3], hash };
  }
  return null;
}

export function validateState(
  value: unknown,
  identity: RouteIdentity,
): PreviewState | null {
  if (!value || typeof value !== 'object') return null;
  const state = value as Record<string, unknown>;
  if (
    state.schemaVersion !== 1
    || state.kind !== identity.kind
    || state.hash !== identity.hash
    || state.semesterSlug !== identity.semesterSlug
    || (identity.kind === 'course' && state.slug !== identity.slug)
    || typeof state.semester !== 'string'
  ) return null;
  return value as PreviewState;
}
