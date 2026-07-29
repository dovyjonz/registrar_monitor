import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import { build } from 'vite';

const websiteRoot = dirname(dirname(fileURLToPath(import.meta.url)));

test('standalone build preserves bundles referenced by generated HTML', async t => {
    const temporaryRoot = await mkdtemp(join(tmpdir(), 'registrar-monitor-vite-'));
    t.after(() => rm(temporaryRoot, { recursive: true, force: true }));

    const outputDir = join(temporaryRoot, 'public');
    const assetsDir = join(outputDir, 'assets');
    const oldAssetReference = 'assets/main-previous.js';
    await mkdir(assetsDir, { recursive: true });
    await writeFile(
        join(outputDir, 'fall2026.html'),
        `<script type="module" src="/${oldAssetReference}"></script>`,
        'utf8',
    );
    await writeFile(join(outputDir, oldAssetReference), 'const previous = true;\n', 'utf8');

    await build({
        configFile: join(websiteRoot, 'vite.config.js'),
        build: { outDir: assetsDir },
    });

    assert.equal(
        await readFile(join(outputDir, oldAssetReference), 'utf8'),
        'const previous = true;\n',
    );

    const manifest = JSON.parse(
        await readFile(join(assetsDir, '.vite', 'manifest.json'), 'utf8'),
    );
    const currentAsset = manifest['src/main.js'].file;
    assert.ok(await readFile(join(assetsDir, currentAsset), 'utf8'));
});
