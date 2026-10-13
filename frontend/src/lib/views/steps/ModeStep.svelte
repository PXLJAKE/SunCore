<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import type { OperatingMode } from '../../api/types';
  import WizardShell from '../../components/WizardShell.svelte';
  import { t, type MessageKey } from '../../i18n/index.svelte';
  import { SaveState, wizard } from '../../stores/wizard.svelte';

  const save = new SaveState();
  const uid = $props.id();
  const MODES: { value: OperatingMode; label: MessageKey; hint: MessageKey }[] = [
    { value: 'shadow', label: 'mode.shadow', hint: 'mode.hint.shadow' },
    { value: 'assisted', label: 'mode.assisted', hint: 'mode.hint.assisted' },
    { value: 'automatic', label: 'mode.automatic', hint: 'mode.hint.automatic' },
  ];

  async function onsave(): Promise<void> {
    await wizard.saveOperation();
  }
</script>

<WizardShell step="mode" intro={t('mode.intro')} {save} {onsave}>
  <p class="notice info" data-testid="mode-note">{t('mode.note_shadow_only')}</p>

  <fieldset class="card">
    <legend>{t('mode.global')}</legend>
    <div class="stack" role="radiogroup" aria-label={t('mode.global')}>
      {#each MODES as m (m.value)}
        <label class="mode">
          <input
            type="radio"
            name="{uid}-global"
            value={m.value}
            checked={wizard.operation.mode === m.value}
            onchange={() => (wizard.operation.mode = m.value)}
          />
          <span>
            <span class="name">{t(m.label)}</span>
            {#if m.value !== 'shadow'}<span class="chip-tag">{t('mode.later')}</span>{/if}
            <span class="hint block">{t(m.hint)}</span>
          </span>
        </label>
      {/each}
    </div>
  </fieldset>

  <fieldset class="card">
    <legend>{t('mode.devices')}</legend>
    <p class="hint">{t('mode.devices_hint')}</p>
    {#each wizard.devices as d (d.id)}
      <div class="field">
        <label for="{uid}-{d.id}">{d.name}</label>
        <select
          id="{uid}-{d.id}"
          class="input"
          value={wizard.operation.deviceModes[d.id] ?? 'shadow'}
          onchange={(e) =>
            (wizard.operation.deviceModes[d.id] = e.currentTarget.value as OperatingMode)}
        >
          {#each MODES as m (m.value)}
            <option value={m.value}
              >{t(m.label)}{m.value === 'shadow' ? '' : ` (${t('mode.later')})`}</option
            >
          {/each}
        </select>
      </div>
    {:else}
      <p class="hint">{t('mode.no_devices')}</p>
    {/each}
  </fieldset>
</WizardShell>

<style>
  .mode {
    display: flex;
    gap: 10px;
    align-items: flex-start;
    min-height: 44px;
  }
  .mode input {
    width: 20px;
    height: 20px;
    margin-top: 2px;
    flex: none;
  }
  .name {
    font-weight: 700;
    margin-inline-end: 8px;
  }
  .block {
    display: block;
  }
</style>
