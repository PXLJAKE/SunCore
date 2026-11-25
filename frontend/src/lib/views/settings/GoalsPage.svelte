<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import { pageRows } from '../../settings/summary';
  import { wizard } from '../../stores/wizard.svelte';
  import StepRows from './StepRows.svelte';

  const rows = $derived(pageRows('goals', wizard.saved));
</script>

<StepRows {rows} />
