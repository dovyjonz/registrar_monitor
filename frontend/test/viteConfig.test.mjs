import test from 'node:test';
import assert from 'node:assert/strict';
import { access, mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { build } from 'vite';

test('package scripts cannot bypass the validated Pages deploy path', async () => {
    const packageJson = JSON.parse(
        await readFile(new URL('../package.json', import.meta.url), 'utf8'),
    );

    assert.equal(packageJson.scripts.deploy, undefined);
    await assert.rejects(access(new URL('../worker.js', import.meta.url)));
});

test('standalone builds preserve bundles referenced by generated HTML', async () => {
    const configSource = await readFile(new URL('../vite.config.js', import.meta.url), 'utf8');
    assert.match(configSource, /emptyOutDir:\s*false/);

    const root = await mkdtemp(join(tmpdir(), 'registrar-monitor-vite-'));
    const output = join(root, 'public', 'assets');
    const entry = join(root, 'main.js');
    const oldBundle = join(output, 'main-old.js');

    try {
        await mkdir(output, { recursive: true });
        await writeFile(entry, 'export const version = "new";\n');
        await writeFile(oldBundle, 'export const version = "old";\n');

        await build({
            configFile: false,
            logLevel: 'silent',
            build: {
                outDir: output,
                emptyOutDir: false,
                manifest: true,
                rollupOptions: { input: { main: entry } },
            },
        });

        assert.equal(await readFile(oldBundle, 'utf8'), 'export const version = "old";\n');
        const manifest = JSON.parse(
            await readFile(join(output, '.vite', 'manifest.json'), 'utf8'),
        );
        assert.equal(Object.values(manifest).length, 1);
        assert.match(
            Object.values(manifest)[0].file,
            /^(?:assets\/)?main-[A-Za-z0-9_-]+\.js$/,
        );
    } finally {
        await rm(root, { recursive: true, force: true });
    }
});
