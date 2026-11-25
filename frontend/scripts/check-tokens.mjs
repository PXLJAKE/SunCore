// SPDX-License-Identifier: Apache-2.0
// Guard of the design system (ADR-0019): colour literals (hex, rgb(), hsl()) belong in the token
// files only. Everything else uses the `--sc-*` tokens. Test files are exempt (they pass explicit
// colours to the pure chart builders).
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = fileURLToPath(new URL('..', import.meta.url));
const SRC = join(ROOT, 'src');

/** Files that may hold colour literals. */
export const ALLOWED = new Set([
    'src/lib/theme/tokens.css',
    'src/lib/theme/tokens.ts',
    'src/lib/theme/bridge.ts',
]);

// `#abc`, `#aabbcc`, `#aabbccdd` (not an HTML entity `&#160;`), and rgb()/rgba()/hsl()/hsla() with
// a literal first argument (code that builds a colour from numbers at runtime is fine).
const PATTERNS = [
    /(?<![&\w])#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})\b/g,
    /\b(?:rgba?|hsla?)\(\s*[\d.]/g,
];

/** Colour literals in `text`, as `line:column match`. */
export function findLiterals(text) {
    const hits = [];
    const lines = text.split('\n');
    lines.forEach((line, i) => {
        for (const re of PATTERNS) {
            re.lastIndex = 0;
            for (const m of line.matchAll(re)) hits.push(`${i + 1}:${(m.index ?? 0) + 1} ${m[0]}`);
        }
    });
    return hits;
}

function* walk(dir) {
    for (const name of readdirSync(dir)) {
        const path = join(dir, name);
        if (statSync(path).isDirectory()) {
            if (name === 'locales') continue; // translations, no styles
            yield* walk(path);
        } else if (/\.(svelte|ts|css)$/.test(name) && !/\.test\.ts$/.test(name)) {
            yield path;
        }
    }
}

function main() {
    let bad = 0;
    for (const file of walk(SRC)) {
        const rel = relative(ROOT, file).split('\\').join('/');
        if (ALLOWED.has(rel) || rel.endsWith('.d.ts')) continue;
        for (const hit of findLiterals(readFileSync(file, 'utf-8'))) {
            console.error(
                `${rel}:${hit}  colour literal outside the token files (use a --sc-* token)`,
            );
            bad += 1;
        }
    }
    if (bad > 0) {
        console.error(`check-tokens: ${bad} colour literal(s) found`);
        process.exit(1);
    }
    console.log('check-tokens: no colour literals outside the token files');
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) main();
