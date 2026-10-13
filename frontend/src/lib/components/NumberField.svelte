<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import type { ValidationIssue } from '../api/types';
  import { num } from '../wizard/forms';
  import FieldIssues from './FieldIssues.svelte';

  interface Props {
    label: string;
    value: number | null;
    /** Unit symbol shown after the input (W, kWp, %); never a translatable word. */
    unit?: string;
    min?: number;
    max?: number;
    step?: number | 'any';
    hint?: string;
    issues?: readonly ValidationIssue[];
    disabled?: boolean;
  }
  let {
    label,
    value = $bindable(),
    unit,
    min,
    max,
    step = 'any',
    hint,
    issues = [],
    disabled = false,
  }: Props = $props();

  const uid = $props.id();
</script>

<div class="field">
  <label for="{uid}-in">{label}</label>
  <div class="with-unit">
    <input
      id="{uid}-in"
      class="input"
      type="number"
      inputmode="decimal"
      {min}
      {max}
      {step}
      {disabled}
      value={value ?? ''}
      aria-invalid={issues.length > 0 ? 'true' : undefined}
      aria-describedby={[hint ? `${uid}-hint` : '', issues.length > 0 ? `${uid}-err` : '']
        .filter(Boolean)
        .join(' ') || undefined}
      oninput={(e) => (value = num(e.currentTarget.valueAsNumber))}
    />
    {#if unit}<span class="unit" aria-hidden="true">{unit}</span>{/if}
  </div>
  {#if hint}<p class="hint" id="{uid}-hint">{hint}</p>{/if}
  <FieldIssues {issues} id="{uid}-err" />
</div>

<style>
  .field {
    display: grid;
    gap: 6px;
    min-width: 0;
  }
  label {
    font-weight: 600;
  }
  .with-unit {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .input {
    max-width: 14rem;
  }
  .unit {
    color: var(--secondary-text-color);
    min-width: 2.5ch;
  }
  .hint {
    margin: 0;
    color: var(--secondary-text-color);
    font-size: 0.875rem;
  }
</style>
