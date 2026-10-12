// SPDX-License-Identifier: Apache-2.0
// Reports the gzip size of dist/ and fails above the budget (Phase 1: 250 kB).
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { gzipSync } from 'node:zlib';

const BUDGET_BYTES = 250 * 1000;
const dist = new URL('../dist/', import.meta.url).pathname;

function* walk(dir) {
    for (const name of readdirSync(dir)) {
        const p = join(dir, name);
        if (statSync(p).isDirectory()) yield* walk(p);
        else yield p;
    }
}

let total = 0;
for (const file of walk(dist)) {
    if (!/\.(js|css|html|json|svg)$/.test(file)) continue;
    const gz = gzipSync(readFileSync(file), { level: 9 }).length;
    total += gz;
    console.log(`${(gz / 1000).toFixed(1).padStart(8)} kB  ${relative(dist, file)}`);
}
console.log(
    `${(total / 1000).toFixed(1).padStart(8)} kB  total gzip (budget ${BUDGET_BYTES / 1000} kB)`,
);
if (total > BUDGET_BYTES) {
    console.error('Bundle size budget exceeded.');
    process.exit(1);
}
