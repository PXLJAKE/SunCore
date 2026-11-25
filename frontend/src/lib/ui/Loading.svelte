<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import { t } from '../i18n/index.svelte';

  interface Props {
    /** What is loading; defaults to the generic text. */
    label?: string;
    /** `block` reserves room (a view or card), `inline` sits in a line of text. */
    layout?: 'block' | 'inline';
    testid?: string;
  }
  let { label, layout = 'block', testid }: Props = $props();
</script>

<p class="loading {layout}" role="status" data-testid={testid}>
  <span class="spinner" aria-hidden="true"></span>
  <span>{label ?? t('app.loading')}</span>
</p>

<style>
  .loading {
    display: flex;
    align-items: center;
    gap: var(--sc-space-2);
    margin: 0;
    color: var(--sc-text-muted);
    font-size: var(--sc-font-sm);
  }
  .block {
    justify-content: center;
    padding: var(--sc-space-6) var(--sc-space-4);
  }
  .inline {
    display: inline-flex;
  }
  .spinner {
    width: 16px;
    height: 16px;
    border-radius: 50%;
    border: 2px solid var(--sc-outline);
    border-top-color: var(--sc-accent);
    animation: spin 0.9s linear infinite;
  }
  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .spinner {
      animation: none;
    }
  }
</style>
