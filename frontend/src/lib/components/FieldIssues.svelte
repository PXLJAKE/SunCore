<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import type { ValidationIssue } from '../api/types';
  import { tDynamic } from '../i18n/index.svelte';

  interface Props {
    issues: readonly ValidationIssue[];
    /** Referenced by `aria-describedby` of the field. */
    id: string;
  }
  let { issues, id }: Props = $props();
</script>

{#if issues.length > 0}
  <ul class="field-issues" {id} data-testid="field-issues">
    {#each issues as issue, i (i)}
      <li>{tDynamic(issue.key, issue.args, 'api.validation.invalid')}</li>
    {/each}
  </ul>
{/if}

<style>
  .field-issues {
    margin: 0;
    padding: 0;
    list-style: none;
    color: var(--error-color);
    font-size: 0.875rem;
    font-weight: 600;
  }
</style>
