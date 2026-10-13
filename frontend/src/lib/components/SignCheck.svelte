<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import { onMount } from 'svelte';
  import { api } from '../api/client';
  import { fragmentFor } from '../entityFormat';
  import { t, tPercent, tPower } from '../i18n/index.svelte';
  import type { RefField } from '../wizard/forms';
  import UnitBadge from './UnitBadge.svelte';

  type Kind = 'battery' | 'grid' | 'pv';

  interface Props {
    kind: Kind;
    /** Document path of the entity, e.g. `battery.power`, `grid.power`, `pv.0.power`. */
    path: string;
    field: RefField;
  }
  let { kind, path, field }: Props = $props();

  const POLL_MS = 3000;
  /** Below this the flow counts as idle: a question about the sign would be noise. */
  const IDLE_W = 20;

  let watts = $state<number | null>(null);
  let unit = $state('');
  let unitOk = $state<boolean | null>(null);
  let failed = $state(false);
  let answered = $state<'ok' | 'inverted' | null>(null);
  let seq = 0;

  async function refresh(): Promise<void> {
    const entityId = field.entity_id.trim();
    if (entityId === '') {
      watts = null;
      return;
    }
    const mine = ++seq;
    try {
      const res = await api.preview(
        fragmentFor(path, { entity_id: entityId, invert_sign: field.invert_sign }),
      );
      if (mine !== seq) return;
      const item = res.items.find((i) => i.path === path) ?? res.items[0];
      watts = item?.base_value ?? null;
      unit = item?.unit ?? '';
      unitOk = item?.unit_ok ?? null;
      failed = false;
    } catch {
      if (mine === seq) failed = true;
    }
  }

  // A new entity or a flipped sign starts a new question.
  let lastKey = '';
  $effect(() => {
    const key = `${field.entity_id.trim()}|${field.invert_sign}`;
    if (key === lastKey) return;
    if (lastKey.split('|')[0] !== key.split('|')[0]) answered = null;
    lastKey = key;
    void refresh();
  });

  onMount(() => {
    const timer = setInterval(() => {
      if (!document.hidden) void refresh();
    }, POLL_MS);
    return () => {
      clearInterval(timer);
      seq += 1;
    };
  });

  function invert(): void {
    field.invert_sign = !field.invert_sign;
    answered = 'inverted';
  }

  const direction = $derived.by(() => {
    if (watts === null) return 'none';
    if (Math.abs(watts) < IDLE_W) return 'idle';
    if (kind === 'battery') return watts > 0 ? 'charging' : 'discharging';
    if (kind === 'grid') return watts > 0 ? 'importing' : 'exporting';
    return watts > 0 ? 'producing' : 'negative';
  });
  const question = $derived(
    watts === null
      ? ''
      : t(`signcheck.${kind}`, { dir: direction, power: tPower(Math.abs(watts)) }),
  );
  // Percent is not a power: a % sensor in a power role is a unit problem, not a sign problem.
  const percentAsPower = $derived(unit.trim() === '%');
</script>

{#if field.entity_id.trim() !== ''}
  <div class="signcheck" role="group" aria-label={t('signcheck.title')} data-testid="sign-check">
    {#if failed}
      <p class="muted">{t('signcheck.unavailable')}</p>
    {:else if unitOk === false}
      <p class="warn">
        <UnitBadge {unit} ok={false} />
        {percentAsPower
          ? t('signcheck.unit_percent', { value: tPercent(watts ?? 0) })
          : t('signcheck.unit_mismatch')}
      </p>
    {:else if watts === null}
      <p class="muted">{t('signcheck.no_value')}</p>
    {:else}
      <p class="question" data-testid="sign-question">{question}</p>
      {#if direction === 'idle'}
        <p class="muted">{t('signcheck.idle_hint')}</p>
      {:else if direction === 'negative'}
        <p class="warn">{t('signcheck.pv_negative_hint')}</p>
      {/if}
      <div class="actions">
        <button
          type="button"
          class="btn secondary"
          aria-pressed={answered === 'ok'}
          onclick={() => (answered = 'ok')}>{t('signcheck.yes')}</button
        >
        <button type="button" class="btn secondary" data-testid="sign-invert" onclick={invert}
          >{t('signcheck.no_invert')}</button
        >
      </div>
      <p class="status" role="status" aria-live="polite">
        {#if answered === 'ok'}{t('signcheck.confirmed')}{/if}
        {#if answered === 'inverted'}{field.invert_sign
            ? t('signcheck.inverted')
            : t('signcheck.not_inverted')}{/if}
      </p>
    {/if}
  </div>
{/if}

<style>
  .signcheck {
    display: grid;
    gap: 8px;
    padding: 12px;
    border-radius: 8px;
    border: 1px dashed var(--primary-color);
    background: color-mix(in srgb, var(--primary-color) 6%, transparent);
  }
  p {
    margin: 0;
  }
  .question {
    font-weight: 600;
  }
  .muted {
    color: var(--secondary-text-color);
    font-size: 0.875rem;
  }
  .warn {
    color: var(--warning-color);
    font-weight: 600;
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    align-items: center;
  }
  .actions {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }
  .status {
    min-height: 1.2em;
    color: var(--success-color);
    font-size: 0.875rem;
  }
</style>
