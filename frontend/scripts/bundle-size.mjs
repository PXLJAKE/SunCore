// SPDX-License-Identifier: Apache-2.0
// Reports the gzip size of dist/ and fails above the budgets:
//   * everything except the ECharts chunk: 250 kB (Phase 1 budget, unchanged),
//   * first paint (index.html plus its static import graph): 125 kB,
//   * the ECharts chunk (Phase 4: loaded on demand with the plan view only): 200 kB,
//   * ECharts must not be reachable from index.html through static imports.
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { gzipSync } from 'node:zlib';

const BUDGET_BYTES = 250 * 1000;
const FIRST_PAINT_BYTES = 125 * 1000;
const ECHARTS_BYTES = 200 * 1000;
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

let total = 0;
let echarts = 0;
for (const file of walk(dist)) {
    if (!/\.(js|css|html|json|svg)$/.test(file)) continue;
    const size = gz(file);
    if (isEcharts(file)) echarts += size;
    else total += size;
    console.log(
        `${(size / 1000).toFixed(1).padStart(8)} kB  ${relative(dist, file)}${isEcharts(file) ? '  (on demand)' : ''}`,
    );
}

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
console.log(
    `${kb(echarts).padStart(8)}  ECharts chunk, on demand (budget ${ECHARTS_BYTES / 1000} kB)`,
);
console.log(
    `${kb(total).padStart(8)}  total gzip without ECharts (budget ${BUDGET_BYTES / 1000} kB)`,
);

let failed = false;
const fail = (msg) => {
    console.error(msg);
    failed = true;
};
if ([...firstPaint].some(isEcharts)) fail('ECharts is part of the first paint.');
if (paint > FIRST_PAINT_BYTES) fail('First paint budget exceeded.');
if (echarts > ECHARTS_BYTES) fail('ECharts chunk budget exceeded.');
if (total > BUDGET_BYTES) fail('Bundle size budget exceeded.');
if (failed) process.exit(1);
