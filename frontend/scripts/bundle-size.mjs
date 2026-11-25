// SPDX-License-Identifier: Apache-2.0
// Reports the gzip size of dist/ and fails above the budgets:
//   * standard user path, everything except ECharts and the expert extras: 250 kB (Phase 1
//     budget, unchanged); only the larger main language catalogue (`catalogue-en|de`) counts,
//     because the app loads just the active language,
//   * expert extras: 30 kB. These are the lazy chunks that only expert mode loads: the
//     `ExpertArea` and `WhatIf` entries, the parameter catalogue (`catalogue-params`, both
//     languages in one chunk) and every chunk only they import. They are found from the Vite
//     manifest (`dist/.vite/manifest.json`) by source file, see `bundle-classify.mjs`, never by
//     hashed file names. Budget split pending maintainer decision; option A implemented
//     (before the split the single total was 262.3 kB against 250 kB),
//   * first paint (index.html plus its static import graph): 125 kB,
//   * every ECharts chunk (loaded on demand): 200 kB, and the chunks of one view together
//     (`echarts-core` plus `echarts-plan` or `echarts-history`): 240 kB (Phase 5, decision 6),
//   * ECharts must not be reachable from index.html through static imports.
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { gzipSync } from 'node:zlib';
import { expertFiles } from './bundle-classify.mjs';

const BUDGET_BYTES = 250 * 1000;
const EXPERT_BYTES = 30 * 1000;
const FIRST_PAINT_BYTES = 125 * 1000;
const ECHARTS_BYTES = 200 * 1000;
const ECHARTS_VIEW_BYTES = 240 * 1000;
const dist = new URL('../dist/', import.meta.url).pathname;

function* walk(dir) {
    for (const name of readdirSync(dir)) {
        const p = join(dir, name);
        if (statSync(p).isDirectory()) yield* walk(p);
        else yield p;
    }
}

const gz = (file) => gzipSync(readFileSync(file), { level: 9 }).length;
const isEcharts = (file) => /(^|\/)echarts-[^/]*\.js$/.test(file);
const isCatalogue = (file) => /(^|\/)catalogue-(en|de)-[^/]*\.js$/.test(file);

const manifest = JSON.parse(readFileSync(join(dist, '.vite/manifest.json'), 'utf8'));
const expertSet = new Set([...expertFiles(manifest)].map((f) => join(dist, f)));
if (expertSet.size === 0) {
    console.error('No expert chunks found in the manifest; update EXPERT_SOURCES.');
    process.exit(1);
}

let total = 0;
let expertTotal = 0;
const catalogues = [];
const chunks = new Map();
for (const file of walk(dist)) {
    if (!/\.(js|css|html|json|svg)$/.test(file) || file.includes('/.vite/')) continue;
    const size = gz(file);
    if (isEcharts(file)) {
        chunks.set(file.replace(/^.*\/echarts-([^./-]+).*$/, '$1'), size);
    } else if (expertSet.has(file)) expertTotal += size;
    else if (isCatalogue(file)) catalogues.push(size);
    else total += size;
    console.log(
        `${(size / 1000).toFixed(1).padStart(8)} kB  ${relative(dist, file)}${isEcharts(file) ? '  (on demand)' : ''}${expertSet.has(file) ? '  (expert extras)' : ''}${isCatalogue(file) ? '  (one language counts)' : ''}`,
    );
}
total += Math.max(0, ...catalogues);

// First paint: the files index.html loads and their static imports (dynamic import() excluded).
const firstPaint = new Set();
function visit(file) {
    if (firstPaint.has(file)) return;
    firstPaint.add(file);
    if (!file.endsWith('.js')) return;
    const code = readFileSync(file, 'utf8');
    for (const m of code.matchAll(/(?:\bfrom|\bimport)\s*["'](\.{1,2}\/[^"']+\.js)["']/g)) {
        visit(resolve(dirname(file), m[1]));
    }
}
const html = readFileSync(join(dist, 'index.html'), 'utf8');
firstPaint.add(join(dist, 'index.html'));
for (const m of html.matchAll(/(?:src|href)="\.\/([^"]+\.(?:js|css))"/g)) visit(join(dist, m[1]));
let paint = 0;
for (const file of firstPaint) paint += gz(file);

const kb = (n) => `${(n / 1000).toFixed(1)} kB`;
console.log(
    `${kb(paint).padStart(8)}  first paint (index.html + static imports; budget ${FIRST_PAINT_BYTES / 1000} kB)`,
);
for (const [name, size] of chunks) {
    console.log(
        `${kb(size).padStart(8)}  ECharts chunk ${name} (budget ${ECHARTS_BYTES / 1000} kB)`,
    );
}
const core = chunks.get('core') ?? 0;
const views = [...chunks].filter(([name]) => name !== 'core');
for (const [name, size] of views) {
    console.log(
        `${kb(core + size).padStart(8)}  ECharts core + ${name} (budget ${ECHARTS_VIEW_BYTES / 1000} kB)`,
    );
}
console.log(
    `${kb(total).padStart(8)}  standard path gzip without ECharts and expert extras (budget ${BUDGET_BYTES / 1000} kB)`,
);
console.log(
    `${kb(expertTotal).padStart(8)}  expert extras gzip (budget ${EXPERT_BYTES / 1000} kB)`,
);

let failed = false;
const fail = (msg) => {
    console.error(msg);
    failed = true;
};
if ([...firstPaint].some(isEcharts)) fail('ECharts is part of the first paint.');
if (paint > FIRST_PAINT_BYTES) fail('First paint budget exceeded.');
for (const [name, size] of chunks) {
    if (size > ECHARTS_BYTES) fail(`ECharts chunk ${name} budget exceeded.`);
}
for (const [name, size] of views) {
    if (core + size > ECHARTS_VIEW_BYTES) fail(`ECharts core + ${name} budget exceeded.`);
}
if (total > BUDGET_BYTES) fail('Bundle size budget exceeded.');
if (expertTotal > EXPERT_BYTES) fail('Expert extras budget exceeded.');
if (failed) process.exit(1);
