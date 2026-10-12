// SPDX-License-Identifier: Apache-2.0
// Local ESLint rule for Svelte templates (ADR-0007): no human-readable literals in markup.
//
// Reports
//  - raw text nodes containing a letter           <p>Hello</p>
//  - string literals in mustache tags             {'Hello'}  {`Hello`}
//  - literal values of accessible-name attributes aria-label="..." title="..." placeholder alt
//    (and {'...'} inside them)
// Text without letters (separators, symbols, digits) is allowed. Everything user-facing goes
// through t() from src/lib/i18n.

const TEXT_ATTRIBUTES = new Set([
  'aria-label',
  'aria-description',
  'aria-placeholder',
  'aria-roledescription',
  'aria-valuetext',
  'title',
  'placeholder',
  'alt',
  'label',
]);

const HAS_LETTER = /\p{L}/u;

/** @param {import('estree').Node | null | undefined} node */
function literalText(node) {
  if (!node) return null;
  if (node.type === 'Literal' && typeof node.value === 'string') return node.value;
  if (node.type === 'TemplateLiteral' && node.expressions.length === 0) {
    return node.quasis.map((q) => q.value.cooked ?? '').join('');
  }
  return null;
}

/** @type {import('eslint').Rule.RuleModule} */
export default {
  meta: {
    type: 'problem',
    docs: { description: 'Disallow hardcoded UI text in Svelte markup; use t().' },
    schema: [],
    messages: {
      text: 'Hardcoded text "{{text}}" in markup. Use t() and add the key to en.json and de.json.',
      attribute:
        'Hardcoded text "{{text}}" in the "{{name}}" attribute. Use t() and add the key to the catalogues.',
      mustache: 'Hardcoded string "{{text}}" in an expression. Use t() instead.',
    },
  },
  create(context) {
    const trim = (s) => s.replace(/\s+/g, ' ').trim();
    return {
      SvelteText(node) {
        const parent = node.parent;
        // <script> and <style> bodies are not markup.
        if (
          parent &&
          (parent.type === 'SvelteScriptElement' || parent.type === 'SvelteStyleElement')
        ) {
          return;
        }
        if (parent && parent.type === 'SvelteAttribute') return; // handled below
        if (HAS_LETTER.test(node.value)) {
          context.report({ node, messageId: 'text', data: { text: trim(node.value) } });
        }
      },
      SvelteMustacheTag(node) {
        // Attribute values are handled by the SvelteAttribute visitor for named attributes;
        // everywhere else a string literal that contains a letter is UI text.
        if (node.parent && node.parent.type === 'SvelteAttribute') return;
        const text = literalText(node.expression);
        if (text !== null && HAS_LETTER.test(text)) {
          context.report({ node, messageId: 'mustache', data: { text: trim(text) } });
        }
      },
      SvelteAttribute(node) {
        const name = node.key && node.key.name;
        if (typeof name !== 'string' || !TEXT_ATTRIBUTES.has(name)) return;
        for (const part of node.value) {
          const text = part.type === 'SvelteLiteral' ? part.value : literalText(part.expression);
          if (text !== null && HAS_LETTER.test(text)) {
            context.report({
              node: part,
              messageId: 'attribute',
              data: { name, text: trim(text) },
            });
          }
        }
      },
    };
  },
};
