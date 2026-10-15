<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import { t } from '../i18n/index.svelte';
  import { live } from '../stores/live.svelte';
</script>

{#if live.control?.emergency_stop}
  <div class="estop-banner" role="status" data-testid="estop-banner">
    <strong>{t('estop.active')}</strong>
    <span>{t('estop.active_text')}</span>
  </div>
{/if}

<style>
  .estop-banner {
    display: flex;
    flex-wrap: wrap;
    gap: 4px 12px;
    padding: 10px 16px;
    background: var(--error-color);
    color: var(--card-background-color);
  }
</style>
