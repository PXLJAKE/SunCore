// SPDX-License-Identifier: Apache-2.0
// Fails if src/lib/api/schema.d.ts is not what `npm run gen:api` would produce.
import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const root = new URL('..', import.meta.url).pathname;
const tmp = mkdtempSync(join(tmpdir(), 'suncore-api-'));
const out = join(tmp, 'schema.d.ts');
try {
    execFileSync(
        join(root, 'node_modules/.bin/openapi-typescript'),
        [join(root, 'src/lib/api/openapi.json'), '-o', out],
        { stdio: 'ignore' },
    );
    const fresh = readFileSync(out, 'utf8');
    const committed = readFileSync(join(root, 'src/lib/api/schema.d.ts'), 'utf8');
    if (fresh !== committed) {
        console.error('src/lib/api/schema.d.ts is out of date; run `npm run gen:api`.');
        process.exit(1);
    }
    console.log('schema.d.ts is up to date.');
} finally {
    rmSync(tmp, { recursive: true, force: true });
}
