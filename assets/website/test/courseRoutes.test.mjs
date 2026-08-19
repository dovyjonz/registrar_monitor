import assert from 'node:assert/strict';
import test from 'node:test';

import { createCourseRouteResolver } from '../src/courseRoutes.mjs';

test('course route resolver keeps archived routes clean and versions live routes', async () => {
    const requested = [];
    const resolveRoute = createCourseRouteResolver(async path => {
        requested.push(path);
        return 'Abcd_123';
    });

    assert.deepEqual(
        await resolveRoute({ semesterSlug: 'fall-2026', courseCode: 'CS 101', archived: true }),
        {
            cleanPath: '/courses/fall-2026/cs-101/',
            sharePath: '/courses/fall-2026/cs-101/',
            archived: true,
        },
    );
    assert.deepEqual(
        await resolveRoute({ semesterSlug: 'fall-2026', courseCode: 'CS 101' }),
        {
            cleanPath: '/courses/fall-2026/cs-101/',
            sharePath: '/courses/fall-2026/cs-101/?v=Abcd_123',
            archived: false,
        },
    );
    assert.deepEqual(requested, ['/courses/fall-2026/cs-101/']);
});

test('course route resolver validates identities and retries failed requests', async () => {
    let calls = 0;
    const resolveRoute = createCourseRouteResolver(async () => {
        calls += 1;
        return calls === 1 ? 'invalid' : 'Abcd_123';
    });
    const input = { semesterSlug: 'fall-2026', courseCode: 'CS 101' };

    await assert.rejects(resolveRoute(input), /valid preview identity/);
    assert.equal((await resolveRoute(input)).sharePath, '/courses/fall-2026/cs-101/?v=Abcd_123');
    assert.equal(calls, 2);
});
