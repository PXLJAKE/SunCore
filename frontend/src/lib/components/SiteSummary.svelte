<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import type { DeviceControlState, SiteControlState } from '../api/types';
  import { allocationSplit, qualityKey } from '../control/present';
  import { t, tPower } from '../i18n/index.svelte';

  interface Props {
    site: SiteControlState;
    devices?: readonly DeviceControlState[];
  }
  let { site, devices = [] }: Props = $props();
  const split = $derived(allocationSplit(site.allocated_draw_w, devices));
  const bad = $derived(qualityKey(site.quality));
</script>

<section class="card summary" aria-labelledby="site-title" data-testid="site-summary">
  <h3 id="site-title">{t('site.title')}</h3>
  {#if bad}<p class="chip-tag warn" data-testid="site-quality">{t(bad)}</p>{/if}
  <dl>
    <div>
      <dt>{t('site.free')}</dt>
      <dd data-testid="site-free">
        {site.free_w == null ? t('site.unknown') : tPower(site.free_w)}
      </dd>
    </div>
    <div>
      <dt>{t('site.deficit')}</dt>
      <dd data-testid="site-deficit" class:bad={site.deficit_w > 0}>{tPower(site.deficit_w)}</dd>
    </div>
    <div>
      <dt>{t('site.running')}</dt>
      <dd data-testid="site-running">{tPower(split.running)}</dd>
    </div>
    {#if split.wouldRun > 0}
      <div>
        <dt>{t('site.would_run')}</dt>
        <dd data-testid="site-would-run">{tPower(split.wouldRun)}</dd>
      </div>
    {/if}
  </dl>
  {#if split.wouldRun > 0}<p class="hint">{t('site.would_run_hint')}</p>{/if}
</section>

<style>
  .summary {
    display: grid;
    gap: 8px;
  }
  .summary > .chip-tag {
    justify-self: start;
    margin: 0;
  }
  dl {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(9rem, 1fr));
    gap: 8px 16px;
    margin: 0;
  }
  dt {
    color: var(--secondary-text-color);
    font-size: 0.875rem;
  }
  dd {
    margin: 0;
    font-size: 1.25rem;
    font-weight: 700;
    font-variant-numeric: tabular-nums;
  }
  dd.bad {
    color: var(--error-color);
  }
</style>
