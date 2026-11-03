import assert from 'node:assert/strict';
import test from 'node:test';

import {
    buildApiRequest,
    initDataFromHash,
    isTelegramLaunch,
    normalizeApiUrl,
    watchControls,
} from '../src/miniappApi.mjs';

test('only Telegram launch parameters count as a Telegram launch', () => {
    const hash = '#tgWebAppData=user%3D1%26hash%3Dabc&tgWebAppVersion=8.0';
    assert.equal(isTelegramLaunch(hash), true);
    assert.equal(initDataFromHash(hash), 'user=1&hash=abc');
    assert.equal(isTelegramLaunch('#CSCI-115'), false);
    assert.equal(isTelegramLaunch(''), false);
    assert.equal(initDataFromHash('#CSCI-115'), '');
});

test('the API URL must be https without a query', () => {
    assert.equal(normalizeApiUrl(' https://bot.example/miniapp/ '), 'https://bot.example/miniapp');
    for (const value of ['', undefined, 'http://bot.example/miniapp', 'https://bot.example/?x=1', 'nope']) {
        assert.equal(normalizeApiUrl(value), '');
    }
});

test('requests carry initData as the credential and nothing else', () => {
    const get = buildApiRequest('https://bot.example/miniapp', 'a=1', 'GET', '/watches');
    assert.equal(get.url, 'https://bot.example/miniapp/watches');
    assert.deepEqual(get.init.headers, { Authorization: 'tma a=1' });
    assert.equal(get.init.credentials, 'omit');
    assert.equal(get.init.body, undefined);

    const post = buildApiRequest('https://bot.example/miniapp', 'a=1', 'POST', '/watches', { course: 'CSCI 115' });
    assert.equal(post.init.headers['Content-Type'], 'application/json');
    assert.equal(post.init.body, '{"course":"CSCI 115"}');
});

const watch = (course, section = '', id = `${course}/${section}`) => ({
    id, semester: 'Fall 2026', course, section,
});
const base = { activeSemester: 'Fall 2026', semester: 'Fall 2026', course: 'CSCI 115', section: '' };

test('an unwatched course offers Watch; a watched one offers Unwatch', () => {
    assert.deepEqual(
        watchControls({ ...base, watches: [] }).map(c => [c.action, c.label]),
        [['add', 'Watch course']],
    );
    const [unwatch] = watchControls({ ...base, watches: [watch('CSCI 115', '', 'w1')] });
    assert.equal(unwatch.action, 'remove');
    assert.equal(unwatch.id, 'w1');
});

test('a selected section can be watched on its own', () => {
    const controls = watchControls({ ...base, section: '1L', watches: [watch('CSCI 115', '2L')] });
    assert.deepEqual(controls.map(c => c.label), ['Watch course', 'Watch section 1L']);
    assert.deepEqual(controls[1].target, { semester: 'Fall 2026', course: 'CSCI 115', section: '1L' });
    assert.equal(controls[1].disabled, false);

    const watched = watchControls({ ...base, section: '1L', watches: [watch('CSCI 115', '1L', 'w2')] });
    assert.deepEqual(watched.map(c => c.label), ['Watch course', 'Unwatch section 1L']);
    assert.equal(watched[1].id, 'w2');
});

test('a course watch already covers its sections', () => {
    const [, section] = watchControls({ ...base, section: '1L', watches: [watch('CSCI 115')] });
    assert.equal(section.disabled, true);
    assert.match(section.hint, /course watch/);
});

test('other semesters and other courses are not watchable or mixed in', () => {
    assert.equal(watchControls({ ...base, semester: 'Spring 2026', watches: [] }), null);
    assert.equal(watchControls({ ...base, activeSemester: null, watches: [] }), null);
    assert.equal(watchControls({ ...base, course: null, watches: [] }), null);
    const [courseControl] = watchControls({ ...base, watches: [watch('MATH 101')] });
    assert.equal(courseControl.action, 'add');
});
