import { test } from 'node:test';
import assert from 'node:assert/strict';
import { searchCourses } from '../src/courseSearch.mjs';

const courses = [
    { code: 'BIO 202', title: 'Computer Biology', instructors: ['José Smith'] },
    { code: 'CSCI 101', title: 'Introduction to Computing', instructors: ['Ada Lovelace'] },
    { code: 'CSCI 102', title: 'Algorithms', instructors: ['José Smith'] },
];

test('search normalizes text and ranks code, title, then instructor with stable ties', () => {
    assert.deepEqual(searchCourses(courses, ' CSCI   101 ').map(r => [r.code, r.reason]), [['CSCI 101', '']]);
    assert.deepEqual(searchCourses(courses, 'csci').map(r => r.code), ['CSCI 101', 'CSCI 102']);
    assert.equal(searchCourses(courses, 'intro comp')[0].reason, 'Introduction to Computing');
    assert.deepEqual(searchCourses(courses, 'JOSE smith').map(r => [r.code, r.reason]), [
        ['BIO 202', 'Prof. José Smith'], ['CSCI 102', 'Prof. José Smith'],
    ]);
    const ranked = searchCourses([
        { code: 'Z 1', title: 'Smith', instructors: [] },
        { code: 'A 1', title: 'Other', instructors: ['Smith'] },
        { code: 'SMITH 1', title: 'Other', instructors: [] },
    ], 'smith');
    assert.deepEqual(ranked.map(r => r.code), ['SMITH 1', 'Z 1', 'A 1']);
});

test('one-edit fuzzy fallback explains the matching field and excludes short typos', () => {
    assert.equal(searchCourses(courses, 'algoritms')[0].reason, 'Algorithms');
    assert.equal(searchCourses(courses, 'lovelcae')[0].reason, 'Prof. Ada Lovelace');
    assert.deepEqual(searchCourses(courses, 'csc'), searchCourses(courses, 'CSC'));
    assert.deepEqual(searchCourses(courses, 'csi'), []);
    assert.deepEqual(searchCourses(courses, 'zzzzzz'), []);
});

test('late and misspelled title matches keep the matching word visible', () => {
    const course = { code: 'BIO 1', title: 'Introduction to advanced topics in computational neuroscience' };
    for (const query of ['neuroscience', 'neuroscince']) {
        assert.equal(searchCourses([course], query)[0].reason, '…neuroscience');
    }
});
