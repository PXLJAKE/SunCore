<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import { onMount } from 'svelte';
  import { gridBadges } from '../grid/present';
  import { t, tPower } from '../i18n/index.svelte';
  import { grid } from '../stores/grid.svelte';

  const badges = $derived(gridBadges(grid.status));

  onMount(() => {
    void grid.refresh();
    const timer = setInterval(() => void grid.refresh(), 15_000);
    return () => clearInterval(timer);
  });
</script>

{#if badges.length > 0}
  <ul class="badges" aria-label={t('grid.badges')} data-testid="grid-badges">
    {#each badges as b (b.kind)}
      {#if b.kind === 'island'}
        <li class="chip-tag warn" data-testid="badge-island">{t('grid.badge.island')}</li>
      {:else if b.kind === 'island_unknown'}
        <li class="chip-tag warn" data-testid="badge-island-unknown">
          {t('grid.badge.island_unknown')}
        </li>
      {:else if b.kind === 'para14a'}
        <li class="chip-tag warn" data-testid="badge-para14a">{t('grid.badge.para14a')}</li>
      {:else if b.kind === 'para14a_unknown'}
        <li class="chip-tag warn" data-testid="badge-para14a-unknown">
          {t('grid.badge.para14a_unknown')}
        </li>
      {:else}
        <li class="chip-tag" class:warn={b.near} data-testid="badge-peak">
          {t('grid.badge.peak', { peak: tPower(b.peakW), limit: tPower(b.limitW ?? 0) })}
        </li>
      {/if}
    {/each}
  </ul>
{/if}

<style>
  .badges {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }
</style>
