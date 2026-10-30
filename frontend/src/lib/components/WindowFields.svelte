<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import { i18n, t } from '../i18n/index.svelte';
  import { toggleDay, toggleMonth, type WindowRow } from '../wizard/scheduleForm';

  interface Props {
    window: WindowRow;
    /** Months 1-12; omit to hide the month selection (empty = all year). */
    months?: number[];
    /** Shown to screen readers and as the group name, e.g. "Window 2". */
    label: string;
  }
  let { window = $bindable(), months = $bindable(), label }: Props = $props();

  const uid = $props.id();
  // 2024-01-01 is a Monday: day 0 = Monday, like the API.
  const dayName = (d: number): string =>
    new Intl.DateTimeFormat(i18n.locale, { weekday: 'short' }).format(new Date(2024, 0, 1 + d));
  const monthName = (m: number): string =>
    new Intl.DateTimeFormat(i18n.locale, { month: 'short' }).format(new Date(2024, m - 1, 1));
</script>

<div class="win" role="group" aria-label={label}>
  <div class="days" role="group" aria-label={t('win.days')}>
    {#each [0, 1, 2, 3, 4, 5, 6] as d (d)}
      <label class="day">
        <input
          type="checkbox"
          checked={window.days.includes(d)}
          onchange={(e) => toggleDay(window, d, e.currentTarget.checked)}
        />
        <span>{dayName(d)}</span>
      </label>
    {/each}
  </div>
  <div class="times">
    <div class="field">
      <label for="{uid}-from">{t('win.from')}</label>
      <input id="{uid}-from" class="input" type="time" bind:value={window.start} />
    </div>
    <div class="field">
      <label for="{uid}-to">{t('win.to')}</label>
      <input id="{uid}-to" class="input" type="time" bind:value={window.end} />
    </div>
  </div>
  {#if months !== undefined}
    <details class="months">
      <summary
        >{months.length === 0
          ? t('win.months_all')
          : t('win.months_some', { count: months.length })}</summary
      >
      <div class="days" role="group" aria-label={t('win.months')}>
        {#each [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12] as m (m)}
          <label class="day">
            <input
              type="checkbox"
              checked={months.includes(m)}
              onchange={(e) => (months = toggleMonth(months ?? [], m, e.currentTarget.checked))}
            />
            <span>{monthName(m)}</span>
          </label>
        {/each}
      </div>
      <p class="hint">{t('win.months_hint')}</p>
    </details>
  {/if}
  {#if window.days.length === 0}
    <p class="hint warnline" role="status">{t('win.no_days')}</p>
  {/if}
</div>

<style>
  .win {
    display: grid;
    gap: 8px;
    min-width: 0;
  }
  .days {
    display: flex;
    flex-wrap: wrap;
    gap: 2px 12px;
  }
  .day {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    min-height: 44px;
  }
  .day input {
    width: 20px;
    height: 20px;
  }
  .times {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(8rem, 1fr));
    gap: 8px 16px;
  }
  summary {
    cursor: pointer;
    min-height: 32px;
    display: flex;
    align-items: center;
    color: var(--primary-color);
    font-weight: 600;
  }
  .warnline {
    color: var(--warning-color);
  }
</style>
