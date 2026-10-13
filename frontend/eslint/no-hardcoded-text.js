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

/**
 * Props of components that are obviously not text (identifiers, enums, URLs, styling hooks).
 * `data-*` is allowed by prefix.
 */
const NON_TEXT_COMPONENT_PROPS = new Set([
  'class',
  'id',
  'href',
  'src',
  'type',
  'name',
  'for',
  'role',
  'rel',
  'target',
  'lang',
  'key',
  'slot',
  'style',
  'variant',
  'kind',
  'size',
  'tone',
  'mode',
  'color',
  'icon',
  'testid',
  'autocomplete',
  'step', // wizard step id
  'path', // document path of a mapped entity
  'unit', // unit symbol (W, kWh), not translatable text
]);

/**
 * All string literals that can end up as text through this expression: literals, template
 * quasis, both branches of ?: and ||/&&/??, and both sides of `+`. Calls (t(...)) are opaque.
 * @param {import('estree').Node | null | undefined} node
 * @returns {string[]}
 */
function literalTexts(node) {
  if (!node) return [];
  switch (node.type) {
    case 'Literal':
      return typeof node.value === 'string' ? [node.value] : [];
    case 'TemplateLiteral':
      return [
        ...node.quasis.map((q) => q.value.cooked ?? ''),
        ...node.expressions.flatMap(literalTexts),
      ];
    case 'ConditionalExpression':
      return [...literalTexts(node.consequent), ...literalTexts(node.alternate)];
    case 'LogicalExpression':
      return [...literalTexts(node.left), ...literalTexts(node.right)];
    case 'BinaryExpression':
      return node.operator === '+' ? [...literalTexts(node.left), ...literalTexts(node.right)] : [];
    case 'TSAsExpression':
    case 'TSNonNullExpression':
    case 'TSSatisfiesExpression':
      return literalTexts(node.expression);
    default:
      return [];
  }
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
        const text = literalTexts(node.expression).find((x) => HAS_LETTER.test(x));
        if (text !== undefined) {
          context.report({ node, messageId: 'mustache', data: { text: trim(text) } });
        }
      },
      SvelteAttribute(node) {
        const name = node.key && node.key.name;
        if (typeof name !== 'string') return;
        const element = node.parent && node.parent.parent;
        const isComponent =
          !!element && element.type === 'SvelteElement' && element.kind === 'component';
        const checked = TEXT_ATTRIBUTES.has(name)
          ? true
          : isComponent && !NON_TEXT_COMPONENT_PROPS.has(name) && !name.startsWith('data-');
        if (!checked) return;
        for (const part of node.value) {
          const texts =
            part.type === 'SvelteLiteral' ? [part.value] : literalTexts(part.expression);
          const text = texts.find((x) => HAS_LETTER.test(x));
          if (text !== undefined) {
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
