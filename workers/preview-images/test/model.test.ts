import { describe, expect, it } from 'vitest';

import { parsePreviewRoute, validateState } from '../src/model';

describe('preview route identity', () => {
  it('accepts exact semester and course shapes', () => {
    expect(parsePreviewRoute('/preview/semester/fall-2026/obLD1OX2.png')).toEqual({
      kind: 'semester', semesterSlug: 'fall-2026', hash: 'obLD1OX2',
    });
    expect(parsePreviewRoute('/preview/course/fall-2026/ant-140/obLD1OX2.png')).toEqual({
      kind: 'course', semesterSlug: 'fall-2026', slug: 'ant-140', hash: 'obLD1OX2',
    });
  });

  it('rejects arbitrary origins and malformed paths', () => {
    expect(parsePreviewRoute('/preview/course/https://evil.test/x/obLD1OX2.png')).toBeNull();
    expect(parsePreviewRoute('/preview/course/fall-2026/ANT-140/obLD1OX2.png')).toBeNull();
    expect(parsePreviewRoute('/preview/course/fall-2026/ant-140/a1b2c3d4e5f6.png')).toBeNull();
    expect(parsePreviewRoute('/preview/course/fall-2026/ant-140/obLD1OX=.png')).toBeNull();
  });

  it('requires state identity to match the immutable route', () => {
    const identity = parsePreviewRoute('/preview/course/fall-2026/ant-140/obLD1OX2.png');
    expect(identity).not.toBeNull();
    expect(validateState({
      schemaVersion: 1,
      kind: 'course',
      hash: 'obLD1OX2',
      semester: 'Fall 2026',
      semesterSlug: 'fall-2026',
      slug: 'ant-140',
    }, identity!)).not.toBeNull();
  });
});
