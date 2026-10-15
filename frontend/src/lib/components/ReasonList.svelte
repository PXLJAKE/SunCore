<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import type { Reason } from '../api/types';
  import { tDynamic } from '../i18n/index.svelte';

  interface Props {
    reasons: readonly Reason[];
  }
  let { reasons }: Props = $props();
</script>

<ul class="reasons">
  {#each reasons as r, i (i)}
    <li>{tDynamic(r.key, r.args)}</li>
  {/each}
</ul>

<style>
  .reasons {
    margin: 0;
    padding-left: 20px;
  }
</style>
