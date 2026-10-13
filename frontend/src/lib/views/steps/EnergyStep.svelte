<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import { onMount } from 'svelte';
  import { api } from '../../api/client';
  import { describeError } from '../../api/errors';
  import type { EnergyPrefs, Suggestion } from '../../api/types';
  import WizardShell from '../../components/WizardShell.svelte';
  import { t, tDynamic, tNumber, type MessageKey } from '../../i18n/index.svelte';
  import { SaveState, wizard } from '../../stores/wizard.svelte';
  import { applySuggestions, isSiteSavable, visibleSuggestions } from '../../wizard/siteForm';

  const save = new SaveState();

  let prefs = $state.raw<EnergyPrefs | null>(null);
  let loadError = $state<string | null>(null);
  let loading = $state(true);
  /** Suggestion path -> picked. Everything starts picked: "accept all" is one click on Next. */
  let picked = $state<Record<string, boolean>>({});

  const suggestions = $derived(prefs ? visibleSuggestions(prefs.suggestions) : []);
  const pickedCount = $derived(suggestions.filter((s) => picked[s.path]).length);

  onMount(() => {
    void (async () => {
      try {
        prefs = await api.energyPrefs();
        picked = Object.fromEntries(
          visibleSuggestions(prefs.suggestions).map((s) => [s.path, true]),
        );
      } catch (e) {
        loadError = describeError(e).key;
      } finally {
        loading = false;
      }
    })();
  });

  function setAll(on: boolean): void {
    picked = Object.fromEntries(suggestions.map((s) => [s.path, on]));
  }

  const LABELS: Record<string, MessageKey> = {
    'grid.power': 'energy.path.grid_power',
    'grid.import_power': 'energy.path.grid_import_power',
    'grid.export_power': 'energy.path.grid_export_power',
    'grid.energy_import': 'energy.path.grid_energy_import',
    'grid.energy_export': 'energy.path.grid_energy_export',
    'battery.power': 'energy.path.battery_power',
    'battery.soc': 'energy.path.battery_soc',
    'battery.capacity_wh': 'energy.path.battery_capacity',
    house: 'energy.path.house',
  };

  function label(s: Suggestion): string {
    const pv = /^pv\.(\d+)\.power$/.exec(s.path);
    if (pv) return t('energy.path.pv_power', { n: Number(pv[1]) + 1 });
    const key = LABELS[s.path];
    return key ? t(key) : s.path;
  }

  function valueText(s: Suggestion): string {
    const v = s.value as { entity_id?: string; invert_sign?: boolean } | number | string | null;
    if (typeof v === 'number')
      return t('energy.value.kwh', { value: tNumber(v / 1000, { maximumFractionDigits: 2 }) });
    if (typeof v === 'string') return v;
    if (v && typeof v === 'object' && typeof v.entity_id === 'string') {
      return v.invert_sign ? t('energy.value.inverted', { entity: v.entity_id }) : v.entity_id;
    }
    return '';
  }

  async function onsave(): Promise<void> {
    if (!prefs || pickedCount === 0) return;
    const set = new Set(Object.keys(picked).filter((p) => picked[p]));
    applySuggestions(wizard.site, prefs.suggestions, set);
    // Without a grid sensor there is nothing to store yet: the grid step completes the draft.
    if (isSiteSavable(wizard.site)) await wizard.saveSite();
  }
</script>

<WizardShell
  step="energy"
  intro={t('energy.intro')}
  {save}
  {onsave}
  skippable
  nextLabel={pickedCount > 0
    ? t('energy.apply', { count: pickedCount })
    : t('action.save_continue')}
>
  {#if loading}
    <p class="hint" role="status">{t('energy.loading')}</p>
  {:else if loadError}
    <p class="notice error" role="alert">{tDynamic(loadError, undefined, 'api.error.unknown')}</p>
  {:else if prefs && !prefs.configured}
    <p class="notice info" data-testid="energy-none">{t('energy.not_configured')}</p>
  {:else if prefs}
    <div class="stack">
      <div class="bulk">
        <button type="button" class="btn secondary small" onclick={() => setAll(true)}
          >{t('energy.select_all')}</button
        >
        <button type="button" class="btn secondary small" onclick={() => setAll(false)}
          >{t('energy.select_none')}</button
        >
        <span class="hint" role="status">{t('energy.selected', { count: pickedCount })}</span>
      </div>
      <ul class="suggestions" data-testid="energy-suggestions">
        {#each suggestions as s (s.path)}
          <li class="suggestion card">
            <label class="check">
              <input type="checkbox" bind:checked={picked[s.path]} />
              <span class="what">{label(s)}</span>
            </label>
            <p class="value">{valueText(s)}</p>
            <p class="hint">{tDynamic(s.reason.key, s.reason.args, 'reason.unknown')}</p>
          </li>
        {:else}
          <li class="hint">{t('energy.empty')}</li>
        {/each}
      </ul>
      {#if prefs.notes.length > 0}
        <div class="notice info" data-testid="energy-notes">
          <p class="notes-title">{t('energy.notes')}</p>
          <ul>
            {#each prefs.notes as note, i (i)}
              <li>{tDynamic(note.key, note.args, 'reason.unknown')}</li>
            {/each}
          </ul>
        </div>
      {/if}
      <p class="hint">{t('energy.never_automatic')}</p>
    </div>
  {/if}
</WizardShell>

<style>
  .bulk {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    align-items: center;
  }
  .suggestions {
    display: grid;
    gap: 8px;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .suggestion {
    display: grid;
    gap: 2px;
    padding: 8px 12px;
  }
  .what {
    font-weight: 600;
  }
  .value {
    margin: 0 0 0 28px;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 0.875rem;
    overflow-wrap: anywhere;
  }
  .suggestion .hint {
    margin-inline-start: 28px;
  }
  .notes-title {
    font-weight: 700;
  }
</style>
