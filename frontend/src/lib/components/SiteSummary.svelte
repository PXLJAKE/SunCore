<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import type { SiteControlState } from '../api/types';
  import { t, tPower } from '../i18n/index.svelte';

  interface Props {
    site: SiteControlState;
  }
  let { site }: Props = $props();
</script>

<section class="card summary" aria-labelledby="site-title" data-testid="site-summary">
  <h3 id="site-title">{t('site.title')}</h3>
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
      <dt>{t('site.allocated')}</dt>
      <dd>{tPower(site.allocated_draw_w)}</dd>
    </div>
  </dl>
</section>

<style>
  .summary {
    display: grid;
    gap: 8px;
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
