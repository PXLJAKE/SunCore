<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import { FormError } from '../../api/errors';
  import NumberField from '../../components/NumberField.svelte';
  import RolePicker from '../../components/RolePicker.svelte';
  import SignCheck from '../../components/SignCheck.svelte';
  import WizardShell from '../../components/WizardShell.svelte';
  import { isUsable } from '../../flow';
  import { t, tPercent, tPower } from '../../i18n/index.svelte';
  import { live } from '../../stores/live.svelte';
  import { SaveState, wizard } from '../../stores/wizard.svelte';
  import { issuesAt } from '../../wizard/issues';
  import { validateSiteForm } from '../../wizard/siteForm';

  const save = new SaveState();
  const UNIT = { kwh: 'kWh', pct: '%', w: 'W' } as const;

  const b = $derived(wizard.site.battery);
  const at = (...p: (string | number)[]) => issuesAt(save.issues, 'battery', ...p);
  const batteryLive = $derived.by(() => {
    const p = live.flows?.battery_w;
    const s = live.flows?.battery_soc_pct;
    if (!isUsable(p)) return t('battery.live_none');
    return isUsable(s)
      ? t('battery.live_value', { power: tPower(p.value), soc: tPercent(s.value) })
      : tPower(p.value);
  });

  async function onsave(): Promise<void> {
    if (b.enabled && validateSiteForm(wizard.site).includes('battery_incomplete')) {
      throw new FormError(['battery.error.incomplete']);
    }
    await wizard.saveSite();
  }
</script>

<WizardShell step="battery" intro={t('battery.intro')} {save} {onsave}>
  <fieldset class="card">
    <legend>{t('battery.present')}</legend>
    <div class="radios" role="radiogroup" aria-label={t('battery.present')}>
      <label class="radio">
        <input
          type="radio"
          name="battery-present"
          checked={b.enabled}
          onchange={() => (wizard.site.battery.enabled = true)}
        />
        {t('battery.yes')}
      </label>
      <label class="radio">
        <input
          type="radio"
          name="battery-present"
          data-testid="no-battery"
          checked={!b.enabled}
          onchange={() => (wizard.site.battery.enabled = false)}
        />
        {t('battery.none')}
      </label>
    </div>
  </fieldset>

  {#if b.enabled}
    <fieldset class="card">
      <legend>{t('battery.sensors')}</legend>
      <p class="hint">{t('battery.live', { value: batteryLive })}</p>
      <RolePicker
        label={t('battery.power')}
        role="battery_power"
        bind:field={wizard.site.battery.power}
        signed
        hint={t('battery.power_hint')}
        issues={at('power')}
      />
      <SignCheck kind="battery" path="battery.power" field={wizard.site.battery.power} />
      <RolePicker
        label={t('battery.soc')}
        role="battery_soc"
        bind:field={wizard.site.battery.soc}
        issues={at('soc')}
      />
    </fieldset>

    <fieldset class="card">
      <legend>{t('battery.limits')}</legend>
      <div class="cols">
        <NumberField
          label={t('battery.capacity')}
          bind:value={wizard.site.battery.capacityKwh}
          unit={UNIT.kwh}
          min={0}
          hint={t('battery.capacity_hint')}
          issues={at('capacity_wh')}
        />
        <NumberField
          label={t('battery.min_soc')}
          bind:value={wizard.site.battery.minSoc}
          unit={UNIT.pct}
          min={0}
          max={100}
          issues={at('min_soc_pct')}
        />
        <NumberField
          label={t('battery.max_soc')}
          bind:value={wizard.site.battery.maxSoc}
          unit={UNIT.pct}
          min={0}
          max={100}
          issues={at('max_soc_pct')}
        />
        <NumberField
          label={t('battery.max_charge')}
          bind:value={wizard.site.battery.maxChargeW}
          unit={UNIT.w}
          min={0}
          issues={at('max_charge_w')}
        />
        <NumberField
          label={t('battery.max_discharge')}
          bind:value={wizard.site.battery.maxDischargeW}
          unit={UNIT.w}
          min={0}
          issues={at('max_discharge_w')}
        />
      </div>
      {#if at().length > 0 && at('min_soc_pct').length === 0}
        <p class="hint">{t('battery.limits_hint')}</p>
      {/if}
    </fieldset>

    <p class="notice info" data-testid="observe-note">{t('battery.observe_note')}</p>
  {/if}
</WizardShell>
