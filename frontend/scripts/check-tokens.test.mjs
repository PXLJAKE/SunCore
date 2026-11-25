// SPDX-License-Identifier: Apache-2.0
// @vitest-environment node
import { describe, expect, it } from 'vitest';
import { ALLOWED, findLiterals } from './check-tokens.mjs';

describe('findLiterals', () => {
    it('finds hex, rgb() and hsl() literals with their position', () => {
        const text = [
            'a { color: #fff; }',
            'b { background: rgb(0 0 0 / 0.5); }',
            'c: hsl(10 20% 30%)',
        ];
        expect(findLiterals(text.join('\n'))).toEqual(['1:12 #fff', '2:17 rgb(0', '3:4 hsl(1']);
    });

    it('accepts tokens, hash routes, entities and colours built at runtime', () => {
        const text = [
            'color: var(--sc-text);',
            "href='#/decisions'",
            'a &#160; b',
            'return `rgba(${r}, ${g}, ${b}, ${a})`;',
            'id="#{uid}"',
            'const anchor = "#settings"',
        ].join('\n');
        expect(findLiterals(text)).toEqual([]);
    });

    it('allows the token files and the bridge only', () => {
        expect([...ALLOWED].sort()).toEqual([
            'src/lib/theme/bridge.ts',
            'src/lib/theme/tokens.css',
            'src/lib/theme/tokens.ts',
        ]);
    });
});
