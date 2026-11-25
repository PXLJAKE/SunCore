<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import { t } from '../i18n/index.svelte';
  import { live } from '../stores/live.svelte';
  import Banner from '../ui/Banner.svelte';
</script>

{#if live.control?.emergency_stop}
  <Banner tone="error" title={t('estop.active')} testid="estop-banner"
    >{t('estop.active_text')}</Banner
  >
{/if}
