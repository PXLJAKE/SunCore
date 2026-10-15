<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import type { OperatingMode } from '../../api/types';
  import { FormError } from '../../api/errors';
  import ConfirmDialog from '../../components/ConfirmDialog.svelte';
  import WizardShell from '../../components/WizardShell.svelte';
  import { t, type MessageKey } from '../../i18n/index.svelte';
  import { live } from '../../stores/live.svelte';
  import { SaveState, wizard } from '../../stores/wizard.svelte';

  const save = new SaveState();
  const uid = $props.id();
  const MODES: { value: OperatingMode; label: MessageKey; hint: MessageKey }[] = [
    { value: 'shadow', label: 'mode.shadow', hint: 'mode.hint.shadow' },
    { value: 'assisted', label: 'mode.assisted', hint: 'mode.hint.assisted' },
    { value: 'automatic', label: 'mode.automatic', hint: 'mode.hint.automatic' },
  ];
  const LABEL: Record<OperatingMode, MessageKey> = {
    shadow: 'mode.shadow',
    assisted: 'mode.assisted',
    automatic: 'mode.automatic',
  };

  /** Device mode as the running controller has it; the stored form only before the first state. */
  const modeOf = (id: string): OperatingMode =>
    live.control?.devices.find((d) => d.device_id === id)?.device_mode ??
    wizard.operation.deviceModes[id] ??
    'shadow';
  // Without a live control state yet, fall back to the saved per-device modes.
  const automaticNames = $derived(
    wizard.devices.filter((d) => modeOf(d.id) === 'automatic').map((d) => d.name),
  );

  // Raising the overall mode to automatic is confirmed first: devices already set to automatic
  // start acting at once.
  let asking = $state(false);
  let answer: ((yes: boolean) => void) | null = null;
  const ask = (): Promise<boolean> =>
    new Promise((resolve) => {
      answer = resolve;
      asking = true;
    });
  function decide(yes: boolean): void {
    asking = false;
    answer?.(yes);
    answer = null;
  }

  async function onsave(): Promise<void> {
    const current = wizard.saved.operation?.mode ?? 'shadow';
    if (wizard.operation.mode === 'automatic' && current !== 'automatic' && !(await ask())) {
      throw new FormError(['mode.confirm_declined']);
    }
    await wizard.saveOperation();
  }
</script>

<WizardShell step="mode" intro={t('mode.intro')} {save} {onsave}>
  <p class="notice info" data-testid="mode-note">{t('mode.note')}</p>

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
            <span class="hint block">{t(m.hint)}</span>
          </span>
        </label>
      {/each}
    </div>
    <p class="hint">{t('mode.global_hint')}</p>
  </fieldset>

  <fieldset class="card">
    <legend>{t('mode.devices')}</legend>
    <p class="hint">{t('mode.devices_hint')}</p>
    <ul class="devices" data-testid="mode-devices">
      {#each wizard.devices as d (d.id)}
        <li>
          <span>{d.name}</span>
          <span class="chip-tag">{t(LABEL[modeOf(d.id)])}</span>
        </li>
      {:else}
        <li class="hint">{t('mode.no_devices')}</li>
      {/each}
    </ul>
    <a href="#/" class="link">{t('mode.open_now')}</a>
  </fieldset>
</WizardShell>

<ConfirmDialog
  open={asking}
  title={t('mode.confirm_title')}
  confirmLabel={t('mode.confirm_action')}
  danger
  onconfirm={() => decide(true)}
  oncancel={() => decide(false)}
  testid="mode-dialog"
>
  <p>{t('mode.confirm_text')}</p>
  <p data-testid="mode-dialog-devices">
    {automaticNames.length > 0
      ? t('mode.confirm_devices', { names: automaticNames.join(', ') })
      : t('mode.confirm_none')}
  </p>
</ConfirmDialog>

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
  .devices {
    list-style: none;
    margin: 0;
    padding: 0;
    display: grid;
    gap: 8px;
  }
  .devices li {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
  }
  .link {
    justify-self: start;
  }
</style>
