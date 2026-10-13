// SPDX-License-Identifier: Apache-2.0
// Theme bridge (BRIEF R6): under Home Assistant ingress the panel is an iframe of the HA frontend.
// If the parent document is reachable (same origin), HA's theme variables are copied onto our
// root so the panel follows the user's HA theme. Otherwise nothing changes: the defaults in
// app.css follow `prefers-color-scheme`. Everything is wrapped in try/catch because reading a
// cross-origin or sandboxed parent throws.
//
// UNVERIFIED against a running Home Assistant (see frontend/README.md).

/** HA theme variable -> our token. Our tokens carry HA's names, so the mapping is the identity. */
export const THEME_MAP: readonly (readonly [ha: string, ours: string])[] = [
  ['--primary-color', '--primary-color'],
  ['--primary-text-color', '--primary-text-color'],
  ['--secondary-text-color', '--secondary-text-color'],
  ['--text-primary-color', '--text-primary-color'],
  ['--card-background-color', '--card-background-color'],
  ['--primary-background-color', '--primary-background-color'],
  ['--secondary-background-color', '--secondary-background-color'],
  ['--divider-color', '--divider-color'],
  ['--error-color', '--error-color'],
  ['--warning-color', '--warning-color'],
  ['--success-color', '--success-color'],
  ['--info-color', '--info-color'],
];

export type ThemeVars = Record<string, string>;

/**
 * The mapped variables as set on the parent's root element, or `null` when the parent is this
 * window, cross-origin, sandboxed, or defines none of them.
 */
export function readParentTheme(win: Window): ThemeVars | null {
  try {
    const parent = win.parent;
    if (!parent || parent === win) return null;
    const doc = parent.document; // throws for a cross-origin parent
    const style = parent.getComputedStyle(doc.documentElement);
    const vars: ThemeVars = {};
    for (const [ha, ours] of THEME_MAP) {
      const value = style.getPropertyValue(ha).trim();
      if (value !== '') vars[ours] = value;
    }
    return Object.keys(vars).length > 0 ? vars : null;
  } catch {
    return null;
  }
}

/** Relative luminance (WCAG) of a `#rgb`, `#rrggbb` or `rgb()/rgba()` colour; `null` if unknown. */
export function luminance(color: string): number | null {
  const c = color.trim().toLowerCase();
  let rgb: number[] | null = null;
  const hex = /^#([0-9a-f]{3}|[0-9a-f]{6})$/.exec(c);
  if (hex) {
    const h = hex[1]!.length === 3 ? [...hex[1]!].map((x) => x + x).join('') : hex[1]!;
    rgb = [0, 2, 4].map((i) => Number.parseInt(h.slice(i, i + 2), 16));
  } else {
    const m = /^rgba?\(\s*(\d+)[\s,]+(\d+)[\s,]+(\d+)/.exec(c);
    if (m) rgb = [Number(m[1]), Number(m[2]), Number(m[3])];
  }
  if (!rgb) return null;
  const [r, g, b] = rgb.map((v) => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  }) as [number, number, number];
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** `dark` when the HA background is dark, so native controls and scrollbars match. */
export function schemeOf(vars: ThemeVars): 'light' | 'dark' | null {
  const bg = vars['--primary-background-color'] ?? vars['--card-background-color'];
  const l = bg ? luminance(bg) : null;
  return l === null ? null : l < 0.4 ? 'dark' : 'light';
}

export function applyTheme(root: HTMLElement, vars: ThemeVars): void {
  for (const [name, value] of Object.entries(vars)) root.style.setProperty(name, value);
  const scheme = schemeOf(vars);
  if (scheme) root.style.colorScheme = scheme;
}

export function clearTheme(root: HTMLElement): void {
  for (const [, ours] of THEME_MAP) root.style.removeProperty(ours);
  root.style.removeProperty('color-scheme');
}

/**
 * Start following the parent theme. Returns a stop function. A forced scheme (`?theme=`, which
 * sets `data-theme` on the root) wins over the bridge.
 */
export function startThemeBridge(
  win: Window = window,
  root: HTMLElement = win.document.documentElement,
): () => void {
  if (root.dataset.theme) return () => {};
  const sync = () => {
    const vars = readParentTheme(win);
    if (vars) applyTheme(root, vars);
    else clearTheme(root);
  };
  sync();
  let observer: MutationObserver | null = null;
  try {
    const parent = win.parent;
    if (parent && parent !== win) {
      // HA switches themes by changing style/class on its root element.
      const ParentObserver = (parent as Window & { MutationObserver?: typeof MutationObserver })
        .MutationObserver;
      if (ParentObserver) {
        observer = new ParentObserver(sync);
        observer.observe(parent.document.documentElement, {
          attributes: true,
          attributeFilter: ['style', 'class'],
        });
      }
    }
  } catch {
    observer = null;
  }
  const media = win.matchMedia?.('(prefers-color-scheme: dark)');
  media?.addEventListener?.('change', sync);
  return () => {
    observer?.disconnect();
    media?.removeEventListener?.('change', sync);
  };
}
