<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import { t } from '../i18n/index.svelte';
  import DebugViews from './DebugViews.svelte';
  import ParamForm from './ParamForm.svelte';

  const uid = $props.id();
</script>

<section class="stack" aria-labelledby="{uid}-t" data-testid="expert-area">
  <h3 id="{uid}-t">{t('expert.title')}</h3>
  <p class="hint">{t('expert.intro')}</p>
  <ParamForm />
  <DebugViews />
</section>
