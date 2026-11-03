import assert from 'node:assert/strict';
import test from 'node:test';

import {
    ELECTIVE_CATEGORIES,
    courseMatchesElective,
    getElectiveCategories,
} from '../src/electiveFilters.mjs';

const { SOCIAL_SCIENCE, NATURAL_SCIENCE, HUMANITIES } = ELECTIVE_CATEGORIES;

test('department rules classify social and natural science electives', () => {
    for (const code of ['ANT 101', 'ECON 201', 'PLS 120', 'SOC 101']) {
        assert.deepEqual(getElectiveCategories(code), [SOCIAL_SCIENCE]);
    }
    for (const code of ['BIOL 101', 'CHEM 101', 'PHYS 161', 'GEOL 100']) {
        assert.deepEqual(getElectiveCategories(code), [NATURAL_SCIENCE]);
    }
});

test('humanities department rules exclude only HST 100', () => {
    assert.deepEqual(getElectiveCategories('HST 100'), []);
    for (const code of ['HST 101', 'PHIL 101', 'REL 101', 'WLL 101']) {
        assert.deepEqual(getElectiveCategories(code), [HUMANITIES]);
    }
});

test('the Spring and Fall additions are humanities electives', () => {
    for (const code of [
        'WCS 160', 'WCS 200', 'WCS 230', 'WCS 240', 'WCS 260/WLL 235',
        'WCS 300', 'WCS 301', 'WCS 302', 'WCS 360/WLL 360', 'WCS 361',
        'WCS 362', 'WCS 363', 'WCS 393', 'WCS 394', 'WCS 462', 'WCS 465',
        'TUR 100', 'TUR 230', 'TUR 231', 'TUR 235', 'TUR 271/HST 271',
        'TUR 272/HST 272', 'TUR 280/LING 280', 'TUR 375', 'TUR 411',
        'TUR 451', 'TUR 454', 'TUR 480/LING 480',
    ]) {
        assert.equal(getElectiveCategories(code).includes(HUMANITIES), true, code);
    }
});

test('either-category additions match both social science and humanities', () => {
    for (const code of [
        'LING 131', 'WLL 385/ANT 385', 'ANT 275', 'ANT 306', 'PLS 102',
        'PLS 325', 'PLS 326', 'PLS 329', 'PLS 421', 'PLS 422', 'PLS 426',
        'SOC 325', 'WCS 101', 'WCS 135', 'WCS 201', 'WCS 203', 'WCS 204',
        'WCS 205', 'WCS 206', 'WCS 210', 'WCS 220', 'WCS 250', 'WCS 270',
        'WCS 304', 'WCS 305', 'WCS 390', 'WCS 391', 'WCS 392', 'TUR 455',
        'TUR 555',
    ]) {
        const categories = getElectiveCategories(code);
        assert.equal(categories.includes(SOCIAL_SCIENCE), true, code);
        assert.equal(categories.includes(HUMANITIES), true, code);
    }
});

test('unlisted courses are not electives and all accepts every course', () => {
    assert.deepEqual(getElectiveCategories('CSCI 101'), []);
    assert.equal(courseMatchesElective('CSCI 101', SOCIAL_SCIENCE), false);
    assert.equal(courseMatchesElective('CSCI 101', 'all'), true);
});
