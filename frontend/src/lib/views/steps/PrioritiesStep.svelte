<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import { onMount } from 'svelte';
  import { FormError } from '../../api/errors';
  import NumberField from '../../components/NumberField.svelte';
  import WindowFields from '../../components/WindowFields.svelte';
  import WizardShell from '../../components/WizardShell.svelte';
  import { t } from '../../i18n/index.svelte';
  import { SaveState, wizard } from '../../stores/wizard.svelte';
  import {
    dropIndexWhileDragging,
    moveItem,
    orderedDevices,
    targetFor,
    withPriorities,
    type MoveDirection,
  } from '../../wizard/priorities';
  import { NIGHT_SCHEDULE, TIME_PATTERN } from '../../wizard/goalsForm';
  import { validateBatteryForm } from '../../wizard/batteryForm';
  import { emptyWindow, isWindowValid, scheduleFromApi } from '../../wizard/scheduleForm';

  const save = new SaveState();
  const uid = $props.id();
  const UNIT = { pct: '%' } as const;
  const hasBattery = $derived(wizard.site.battery.enabled || wizard.saved.site?.battery != null);
  const currency = $derived(wizard.region.currency || '');

  let order = $state<string[]>([]);
  let announcement = $state('');
  let list: HTMLUListElement | undefined = $state();
  let drag = $state<{ id: string; from: number; target: number; dy: number } | null>(null);
  let startY = 0;

  onMount(() => {
    order = orderedDevices(wizard.devices).map((d) => d.id);
  });

  const byId = $derived(new Map(wizard.devices.map((d) => [d.id, d])));
  const nameOf = (id: string): string => byId.get(id)?.name ?? id;

  function announce(id: string, to: number): void {
    announcement = t('prio.moved', { name: nameOf(id), position: to + 1, total: order.length });
  }

  function move(id: string, direction: MoveDirection): void {
    const from = order.indexOf(id);
    const to = targetFor(from, order.length, direction);
    if (to === from) {
      announcement = t(
        direction === 'up' || direction === 'first' ? 'prio.at_top' : 'prio.at_bottom',
        {
          name: nameOf(id),
        },
      );
      return;
    }
    order = moveItem(order, from, to);
    announce(id, to);
    // Keep the focus on the control that was used: the row re-renders in place.
    queueMicrotask(() =>
      list?.querySelector<HTMLElement>(`[data-move="${id}-${direction}"]`)?.focus(),
    );
  }

  const dragOffset = (id: string): string | undefined =>
    drag?.id === id ? `translateY(${drag.dy}px)` : undefined;

  function rowCenters(): number[] {
    return [...(list?.querySelectorAll<HTMLElement>('[data-row]') ?? [])].map((el) => {
      const r = el.getBoundingClientRect();
      return r.top + r.height / 2;
    });
  }

  function onPointerDown(e: PointerEvent, id: string): void {
    if (e.button !== 0 && e.pointerType === 'mouse') return;
    const from = order.indexOf(id);
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    startY = e.clientY;
    drag = { id, from, target: from, dy: 0 };
    announcement = t('prio.grabbed', { name: nameOf(id) });
  }

  function onPointerMove(e: PointerEvent): void {
    if (!drag) return;
    const target = dropIndexWhileDragging(rowCenters(), drag.from, e.clientY);
    drag = { ...drag, target, dy: e.clientY - startY };
  }

  function onPointerEnd(e: PointerEvent, cancelled: boolean): void {
    if (!drag) return;
    const { id, from, target } = drag;
    drag = null;
    (e.currentTarget as HTMLElement).releasePointerCapture?.(e.pointerId);
    if (cancelled || target === from) return;
    order = moveItem(order, from, target);
    announce(id, target);
  }

  function onHandleKey(e: KeyboardEvent, id: string): void {
    const dir: Record<string, MoveDirection> = {
      ArrowUp: 'up',
      ArrowDown: 'down',
      Home: 'first',
      End: 'last',
    };
    const d = dir[e.key];
    if (!d) return;
    e.preventDefault();
    const from = order.indexOf(id);
    const to = targetFor(from, order.length, d);
    if (to !== from) {
      order = moveItem(order, from, to);
      announce(id, to);
      queueMicrotask(() => list?.querySelector<HTMLElement>(`[data-handle="${id}"]`)?.focus());
    }
  }

  async function onsave(): Promise<void> {
    if (wizard.goals.targetEnabled && !TIME_PATTERN.test(wizard.goals.targetTime)) {
      throw new FormError(['goals.error.time']);
    }
    if (hasBattery) {
      const errors = validateBatteryForm(wizard.battery, wizard.batteryUsesModbus);
      if (errors.includes('grid_charge_needs_rule')) throw new FormError(['gc.needs_rule']);
      if (wizard.battery.gridChargeSchedule.some((w) => !isWindowValid(w))) {
        throw new FormError(['win.no_days']);
      }
    }
    if (order.length > 0) {
      const next = withPriorities(wizard.devices, order);
      if (next.some((d, i) => d.priority !== wizard.devices[i]!.priority)) {
        await wizard.saveDevices(next);
      }
    }
    await wizard.saveGoals();
    if (hasBattery) await wizard.saveBattery();
  }
</script>

<WizardShell step="priorities" intro={t('prio.intro')} {save} {onsave} skippable>
  <section class="card stack" aria-labelledby="{uid}-prio">
    <h3 id="{uid}-prio">{t('prio.title')}</h3>
    <p class="hint">{t('prio.hint')}</p>
    {#if order.length === 0}
      <p class="hint" data-testid="prio-none">{t('prio.none')}</p>
    {:else}
      <ol class="prio" bind:this={list} data-testid="prio-list">
        {#each order as id, i (id)}
          <li
            data-row
            data-id={id}
            class:dragging={drag?.id === id}
            class:target-before={drag !== null &&
              drag.id !== id &&
              drag.target === i &&
              drag.target < drag.from}
            class:target-after={drag !== null &&
              drag.id !== id &&
              drag.target === i &&
              drag.target > drag.from}
            style:transform={dragOffset(id)}
          >
            <span class="rank" aria-hidden="true">{i + 1}</span>
            <button
              type="button"
              class="handle"
              data-handle={id}
              aria-label={t('prio.handle', {
                name: nameOf(id),
                position: i + 1,
                total: order.length,
              })}
              onpointerdown={(e) => onPointerDown(e, id)}
              onpointermove={onPointerMove}
              onpointerup={(e) => onPointerEnd(e, false)}
              onpointercancel={(e) => onPointerEnd(e, true)}
              onkeydown={(e) => onHandleKey(e, id)}
            >
              <span aria-hidden="true">⋮⋮</span>
            </button>
            <span class="name">{nameOf(id)}</span>
            <span class="moves">
              <button
                type="button"
                class="btn secondary small"
                data-move="{id}-up"
                disabled={i === 0}
                onclick={() => move(id, 'up')}
                aria-label={t('prio.up', { name: nameOf(id) })}
                ><span aria-hidden="true">↑</span></button
              >
              <button
                type="button"
                class="btn secondary small"
                data-move="{id}-down"
                disabled={i === order.length - 1}
                onclick={() => move(id, 'down')}
                aria-label={t('prio.down', { name: nameOf(id) })}
                ><span aria-hidden="true">↓</span></button
              >
            </span>
          </li>
        {/each}
      </ol>
    {/if}
    <p class="sr-only" role="status" aria-live="polite" data-testid="prio-live">{announcement}</p>
    <p class="hint">{t('prio.order_note')}</p>
  </section>

  <fieldset class="card" data-testid="grid-charge">
    <legend>{t('gc.title')}</legend>
    {#if !hasBattery}
      <p class="hint">{t('gc.no_battery')}</p>
    {:else}
      <p class="hint">{t('gc.hint')}</p>
      <label class="check">
        <input
          type="checkbox"
          bind:checked={wizard.battery.gridChargeEnabled}
          data-testid="gc-enable"
        />
        {t('gc.enable')}
      </label>
      {#if wizard.battery.gridChargeEnabled}
        <NumberField
          label={t('gc.max_price', { currency })}
          bind:value={wizard.battery.gridChargeMaxPrice}
          min={-10}
          max={100}
          step={0.001}
          hint={t('gc.max_price_hint')}
        />
        <div class="stack" data-testid="gc-schedule">
          <h4>{t('gc.schedule')}</h4>
          <p class="hint">{t('gc.schedule_hint')}</p>
          <ul class="windows">
            {#each wizard.battery.gridChargeSchedule as _row, i (i)}
              <li>
                <span class="tier-no">{t('win.n', { n: i + 1 })}</span>
                <WindowFields
                  bind:window={
                    () => wizard.battery.gridChargeSchedule[i]!,
                    (v) => (wizard.battery.gridChargeSchedule[i] = v)
                  }
                  label={t('win.n', { n: i + 1 })}
                />
                <button
                  type="button"
                  class="btn secondary small"
                  onclick={() => wizard.battery.gridChargeSchedule.splice(i, 1)}
                  >{t('win.remove', { n: i + 1 })}</button
                >
              </li>
            {/each}
          </ul>
          <p class="hint">{t('win.crosses_midnight')}</p>
          <div class="row">
            <button
              type="button"
              class="btn secondary"
              data-testid="gc-add"
              onclick={() => wizard.battery.gridChargeSchedule.push(emptyWindow())}
              >{t('win.add')}</button
            >
            <button
              type="button"
              class="btn secondary"
              onclick={() => (wizard.battery.gridChargeSchedule = scheduleFromApi(NIGHT_SCHEDULE))}
              >{t('gc.night')}</button
            >
          </div>
        </div>
      {/if}
      <details data-testid="autarky">
        <summary>{t('gc.autarky')}</summary>
        <NumberField
          label={t('gc.autarky')}
          bind:value={wizard.battery.autarkyWeightPerKwh}
          min={0}
          max={1}
          step={0.001}
          hint={t('gc.autarky_hint')}
        />
      </details>
    {/if}
  </fieldset>

  <fieldset class="card">
    <legend>{t('goals.title')}</legend>
    <label class="check">
      <input type="checkbox" bind:checked={wizard.goals.targetEnabled} />
      {t('goals.battery_target')}
    </label>
    {#if wizard.goals.targetEnabled}
      <div class="cols">
        <NumberField
          label={t('goals.target_soc')}
          bind:value={wizard.goals.targetSoc}
          unit={UNIT.pct}
          min={0}
          max={100}
        />
        <div class="field">
          <label for="{uid}-time">{t('goals.target_time')}</label>
          <input id="{uid}-time" class="input" type="time" bind:value={wizard.goals.targetTime} />
        </div>
      </div>
    {/if}
    <label class="check">
      <input type="checkbox" bind:checked={wizard.goals.climateBeforeExport} />
      {t('goals.climate_before_export')}
    </label>
    <p class="hint">{t('goals.climate_hint')}</p>
    <label class="check">
      <input type="checkbox" bind:checked={wizard.goals.gridChargeNightOnly} />
      {t('goals.grid_charge_night')}
    </label>
    <p class="hint">{t('goals.stored_note')}</p>
    {#if hasBattery}<p class="hint">{t('gc.goals_note')}</p>{/if}
  </fieldset>
</WizardShell>

<style>
  .windows {
    display: grid;
    gap: 12px;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .windows li {
    display: grid;
    gap: 8px;
    padding: 12px;
    border: 1px solid var(--divider-color);
    border-radius: 8px;
  }
  .tier-no {
    font-weight: 700;
  }
  .row {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }
  .prio {
    display: grid;
    gap: 8px;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .prio li {
    display: grid;
    grid-template-columns: auto auto 1fr auto;
    align-items: center;
    gap: 10px;
    padding: 6px 10px;
    border: 1px solid var(--divider-color);
    border-radius: 10px;
    background: var(--card-background-color);
    position: relative;
  }
  .prio li.dragging {
    z-index: 2;
    box-shadow: 0 6px 20px rgb(0 0 0 / 0.3);
    border-color: var(--primary-color);
    opacity: 0.95;
  }
  .prio li.target-before {
    box-shadow: 0 -3px 0 var(--primary-color);
  }
  .prio li.target-after {
    box-shadow: 0 3px 0 var(--primary-color);
  }
  .rank {
    min-width: 1.5ch;
    font-weight: 700;
    color: var(--secondary-text-color);
  }
  .handle {
    font: inherit;
    min-width: 44px;
    min-height: 44px;
    border: 1px dashed var(--divider-color);
    border-radius: 8px;
    background: transparent;
    color: var(--secondary-text-color);
    cursor: grab;
    touch-action: none;
    letter-spacing: -2px;
  }
  .handle:active {
    cursor: grabbing;
  }
  .name {
    font-weight: 600;
    overflow-wrap: anywhere;
  }
  .moves {
    display: flex;
    gap: 6px;
  }
  .moves .btn {
    min-width: 44px;
    min-height: 44px;
    padding: 0;
  }
</style>
