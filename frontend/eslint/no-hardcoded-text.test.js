// SPDX-License-Identifier: Apache-2.0
import { RuleTester } from 'eslint';
import svelteParser from 'svelte-eslint-parser';
import { describe, it } from 'vitest';
import rule from './no-hardcoded-text.js';

RuleTester.describe = describe;
RuleTester.it = it;
RuleTester.itOnly = it.only;

const tester = new RuleTester({
  languageOptions: { parser: svelteParser },
});

tester.run('no-hardcoded-text', rule, {
  valid: [
    { code: '<p>{t("a.b")}</p>', filename: 'a.svelte' },
    { code: '<p>  </p>', filename: 'a.svelte' },
    { code: '<p>{value} · {other}</p>', filename: 'a.svelte' },
    { code: '<p>–</p>', filename: 'a.svelte' },
    { code: '<p>42 %</p>', filename: 'a.svelte' },
    { code: '<button aria-label={t("a.b")}>{icon}</button>', filename: 'a.svelte' },
    { code: '<input placeholder={t("a.b")} />', filename: 'a.svelte' },
    { code: '<div class="card" id="x" data-testid="node-pv"></div>', filename: 'a.svelte' },
    { code: '<script>const a = "Hello";</script><p>{a}</p>', filename: 'a.svelte' },
    { code: '<style>.a { color: red; }</style>', filename: 'a.svelte' },
    { code: '<p>{`${n}`}</p>', filename: 'a.svelte' },
  ],
  invalid: [
    {
      code: '<p>Hello</p>',
      filename: 'a.svelte',
      errors: [{ messageId: 'text' }],
    },
    {
      code: '<button>Save</button>',
      filename: 'a.svelte',
      errors: [{ messageId: 'text' }],
    },
    {
      code: '<p>{"Hello"}</p>',
      filename: 'a.svelte',
      errors: [{ messageId: 'mustache' }],
    },
    {
      code: '<p>{`Hello`}</p>',
      filename: 'a.svelte',
      errors: [{ messageId: 'mustache' }],
    },
    {
      code: '<button aria-label="Close">{icon}</button>',
      filename: 'a.svelte',
      errors: [{ messageId: 'attribute' }],
    },
    {
      code: '<div title="Tip"></div>',
      filename: 'a.svelte',
      errors: [{ messageId: 'attribute' }],
    },
    {
      code: '<input placeholder={"Search"} />',
      filename: 'a.svelte',
      errors: [{ messageId: 'attribute' }],
    },
    {
      code: '<img alt="Logo" src="x" />',
      filename: 'a.svelte',
      errors: [{ messageId: 'attribute' }],
    },
    {
      code: '<p>Grüße</p>',
      filename: 'a.svelte',
      errors: [{ messageId: 'text' }],
    },
  ],
});
