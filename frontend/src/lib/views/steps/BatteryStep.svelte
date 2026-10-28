<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import { onMount } from 'svelte';
  import { api } from '../../api/client';
  import type {
    BatteryStrategy,
    BatteryTemplate,
    RiskLevel,
    TemplateSummary,
  } from '../../api/types';
  import { FormError } from '../../api/errors';
  import NumberField from '../../components/NumberField.svelte';
  import RolePicker from '../../components/RolePicker.svelte';
  import SignCheck from '../../components/SignCheck.svelte';
  import WizardShell from '../../components/WizardShell.svelte';
  import { isUsable } from '../../flow';
  import { t, tDynamic, tPercent, tPower, type MessageKey } from '../../i18n/index.svelte';
  import { live } from '../../stores/live.svelte';
  import { SaveState, wizard } from '../../stores/wizard.svelte';
  import { issuesAt } from '../../wizard/issues';
  import { validateSiteForm } from '../../wizard/siteForm';
  import { emptyRef } from '../../wizard/forms';
  import { hasModel, validateBatteryForm, type BatteryFormError } from '../../wizard/batteryForm';

  const save = new SaveState();
  const UNIT = { kwh: 'kWh', pct: '%', w: 'W', s: 's' } as const;

  const b = $derived(wizard.site.battery);
  const at = (...p: (string | number)[]) => issuesAt(save.issues, 'battery', ...p);
  /** Issues of the battery section itself (its fields sit at the document root). */
  const bat = (...p: (string | number)[]) => issuesAt(save.issues, ...p);
  const batteryLive = $derived.by(() => {
    const p = live.flows?.battery_w;
    const s = live.flows?.battery_soc_pct;
    if (!isUsable(p)) return t('battery.live_none');
    return isUsable(s)
      ? t('battery.live_value', { power: tPower(p.value), soc: tPercent(s.value) })
      : tPower(p.value);
  });

  const uid = $props.id();
  const f = $derived(wizard.battery);
  const STRATEGIES: BatteryStrategy[] = ['self_consumption', 'prefer_full', 'target_by_time'];
  const RISKS: RiskLevel[] = ['cautious', 'balanced', 'bold'];
  const STRATEGY_KEY: Record<BatteryStrategy, MessageKey> = {
    self_consumption: 'bat.strategy.self_consumption',
    prefer_full: 'bat.strategy.prefer_full',
    target_by_time: 'bat.strategy.target_by_time',
  };
  const RISK_KEY: Record<RiskLevel, MessageKey> = {
    cautious: 'bat.risk.cautious',
    balanced: 'bat.risk.balanced',
    bold: 'bat.risk.bold',
  };
  const FORM_ERROR: Record<BatteryFormError, MessageKey> = {
    grid_charge_needs_rule: 'gc.needs_rule',
    adapter_entities_missing: 'bat.error.roles',
    modbus_incomplete: 'bat.error.modbus',
    heartbeat_too_slow: 'bat.error.heartbeat',
  };

  let adapters = $state.raw<TemplateSummary[]>([]);
  let detail = $state.raw<BatteryTemplate | null>(null);

  onMount(() => {
    void api
      .templates('battery')
      .then((res) => (adapters = res.templates))
      .catch(() => (adapters = []));
  });

  // The chosen adapter's roles get a field each; its driver decides whether a modbus link is stored.
  $effect(() => {
    const id = wizard.battery.adapterTemplateId;
    if (id === '') {
      detail = null;
      wizard.batteryUsesModbus = false;
      return;
    }
    let stale = false;
    api.template(id).then(
      (res) => {
        if (stale || res.template.kind !== 'battery') return;
        detail = res.template;
        wizard.batteryUsesModbus = res.template.driver === 'kostal_modbus';
        const roles = Object.keys(res.template.roles ?? {});
        // Entities of another adapter's roles do not belong to this one.
        for (const role of Object.keys(wizard.battery.entities)) {
          if (!roles.includes(role)) delete wizard.battery.entities[role];
        }
        for (const role of roles) wizard.battery.entities[role] ??= emptyRef();
      },
      () => {
        if (!stale) detail = null;
      },
    );
    return () => {
      stale = true;
    };
  });

  const driver = $derived(detail?.driver ?? 'generic');
  const summary = $derived(adapters.find((a) => a.id === f.adapterTemplateId));

  async function onsave(): Promise<void> {
    if (b.enabled && validateSiteForm(wizard.site).includes('battery_incomplete')) {
      throw new FormError(['battery.error.incomplete']);
    }
    if (b.enabled) {
      // An empty model starts from the values of the section above.
      const form = wizard.battery;
      form.capacityKwh ??= wizard.site.battery.capacityKwh;
      form.maxChargeW ??= wizard.site.battery.maxChargeW;
      form.maxDischargeW ??= wizard.site.battery.maxDischargeW;
      const errors = validateBatteryForm(form, wizard.batteryUsesModbus);
      if (form.adapterTemplateId !== '') {
        const mapped = Object.values(form.entities).some((r) => r.entity_id.trim() !== '');
        if (!mapped) errors.push('adapter_entities_missing');
      }
      if (errors.length > 0) throw new FormError(errors.map((e) => FORM_ERROR[e]));
    }
    await wizard.saveSite();
    if (b.enabled) await wizard.saveBattery();
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

    <fieldset class="card" data-testid="battery-control">
      <legend>{t('bat.control')}</legend>
      <p class="hint">{t('bat.control_hint')}</p>
      <div class="field">
        <label for="{uid}-adapter">{t('bat.adapter')}</label>
        <select id="{uid}-adapter" class="input" bind:value={wizard.battery.adapterTemplateId}>
          <option value="">{t('bat.adapter_observe')}</option>
          {#each adapters as a (a.id)}
            <option value={a.id}>{tDynamic(a.name, undefined, 'reason.unknown')}</option>
          {/each}
        </select>
      </div>
      {#if f.adapterTemplateId === ''}
        <p class="notice info" data-testid="observe-note">{t('battery.observe_note')}</p>
      {:else}
        {#if f.adapterTemplateId === 'kostal.plenticore'}
          <p class="notice info" data-testid="hint-kostal-rest">{t('bat.hint_kostal_rest')}</p>
        {/if}
        {#if driver === 'kostal_modbus'}
          <p class="notice info" data-testid="hint-kostal-modbus">{t('bat.hint_kostal_modbus')}</p>
        {/if}
        {#if summary && summary.tested_with.length === 0}
          <p class="hint">
            <span class="chip-tag warn">{t('template.untested')}</span>
            {t('bat.hint_untested')}
          </p>
        {/if}
        {#if detail}
          <h4>{t('bat.adapter_roles')}</h4>
          {#each Object.entries(detail.roles ?? {}) as [role, spec] (role)}
            {#if wizard.battery.entities[role]}
              <RolePicker
                label={tDynamic(spec.description, undefined, 'reason.unknown')}
                {role}
                template={detail.id}
                bind:field={wizard.battery.entities[role]!}
                optional={!spec.required}
              />
            {/if}
          {/each}
        {/if}
        {#if driver === 'kostal_modbus'}
          <h4>{t('bat.modbus')}</h4>
          <div class="cols">
            <div class="field">
              <label for="{uid}-hub">{t('bat.modbus_hub')}</label>
              <input
                id="{uid}-hub"
                class="input"
                autocomplete="off"
                bind:value={wizard.battery.modbus.hub}
              />
              <p class="hint">{t('bat.modbus_hub_hint')}</p>
            </div>
            <NumberField
              label={t('bat.modbus_slave')}
              bind:value={wizard.battery.modbus.slave}
              min={1}
              max={255}
              step={1}
              hint={t('bat.modbus_slave_hint')}
            />
            <div class="field">
              <label for="{uid}-order">{t('bat.modbus_order')}</label>
              <select id="{uid}-order" class="input" bind:value={wizard.battery.modbus.byteOrder}>
                <option value="little">{t('bat.order_little')}</option>
                <option value="big">{t('bat.order_big')}</option>
              </select>
              <p class="hint">{t('bat.modbus_order_hint')}</p>
            </div>
            <NumberField
              label={t('bat.modbus_heartbeat')}
              bind:value={wizard.battery.modbus.heartbeatS}
              unit={UNIT.s}
              min={5}
              step={1}
            />
            <NumberField
              label={t('bat.modbus_timeout')}
              bind:value={wizard.battery.modbus.fallbackTimeoutS}
              unit={UNIT.s}
              min={30}
              max={10800}
              step={1}
            />
          </div>
          <p class="hint" data-testid="fallback-state">
            {t(f.fallbackMeasured ? 'bat.fallback_measured' : 'bat.fallback_not_measured')}
          </p>
        {/if}
      {/if}
    </fieldset>

    <fieldset class="card" data-testid="battery-model">
      <legend>{t('bat.model')}</legend>
      <p class="hint">{t('bat.model_hint')}</p>
      <div class="cols">
        <NumberField
          label={t('battery.capacity')}
          bind:value={wizard.battery.capacityKwh}
          unit={UNIT.kwh}
          min={0}
          issues={bat('model', 'capacity_wh')}
        />
        <NumberField
          label={t('battery.max_charge')}
          bind:value={wizard.battery.maxChargeW}
          unit={UNIT.w}
          min={0}
          issues={bat('model', 'max_charge_w')}
        />
        <NumberField
          label={t('battery.max_discharge')}
          bind:value={wizard.battery.maxDischargeW}
          unit={UNIT.w}
          min={0}
          issues={bat('model', 'max_discharge_w')}
        />
        <NumberField
          label={t('bat.reserve')}
          bind:value={wizard.battery.reservePct}
          unit={UNIT.pct}
          min={0}
          max={100}
          hint={t('bat.reserve_hint')}
          issues={bat('model', 'reserve_pct')}
        />
        <NumberField
          label={t('bat.drift')}
          bind:value={wizard.battery.bmsDriftMarginPct}
          unit={UNIT.pct}
          min={0}
          max={100}
          hint={t('bat.drift_hint')}
          issues={bat('model', 'bms_drift_margin_pct')}
        />
        <NumberField
          label={t('bat.max_soc')}
          bind:value={wizard.battery.maxSocPct}
          unit={UNIT.pct}
          min={0}
          max={100}
          issues={bat('model', 'max_soc_pct')}
        />
      </div>
      <details>
        <summary>{t('bat.expert')}</summary>
        <div class="cols">
          <NumberField
            label={t('bat.eff_charge')}
            bind:value={wizard.battery.chargeEfficiencyPct}
            unit={UNIT.pct}
            min={1}
            max={100}
            issues={bat('model', 'charge_efficiency')}
          />
          <NumberField
            label={t('bat.eff_discharge')}
            bind:value={wizard.battery.dischargeEfficiencyPct}
            unit={UNIT.pct}
            min={1}
            max={100}
            issues={bat('model', 'discharge_efficiency')}
          />
          <NumberField
            label={t('bat.degradation')}
            bind:value={wizard.battery.degradationCostPerKwh}
            min={0}
            step={0.001}
            issues={bat('model', 'degradation_cost_per_kwh')}
          />
          <NumberField
            label={t('bat.grid_margin')}
            bind:value={wizard.battery.gridChargeMarginPerKwh}
            min={0}
            step={0.001}
            issues={bat('model', 'grid_charge_margin_per_kwh')}
          />
        </div>
      </details>
      {#if !hasModel(f)}
        <p class="hint" role="status">{t('bat.model_missing')}</p>
      {/if}
    </fieldset>

    <fieldset class="card" data-testid="battery-strategy">
      <legend>{t('bat.strategy')}</legend>
      <div class="radios" role="radiogroup" aria-label={t('bat.strategy')}>
        {#each STRATEGIES as s (s)}
          <label class="radio">
            <input
              type="radio"
              name="{uid}-strategy"
              checked={f.strategy === s}
              onchange={() => (wizard.battery.strategy = s)}
            />
            {t(STRATEGY_KEY[s])}
          </label>
        {/each}
      </div>
      {#if f.strategy === 'target_by_time'}<p class="hint">{t('bat.strategy_hint')}</p>{/if}
      <div class="field" role="radiogroup" aria-labelledby="{uid}-risk">
        <span id="{uid}-risk" class="label">{t('bat.risk')}</span>
        <div class="radios">
          {#each RISKS as r (r)}
            <label class="radio">
              <input
                type="radio"
                name="{uid}-risk"
                checked={f.risk === r}
                onchange={() => (wizard.battery.risk = r)}
              />
              {t(RISK_KEY[r])}
            </label>
          {/each}
        </div>
        <p class="hint">{t('bat.risk_hint')}</p>
      </div>
      <NumberField
        label={t('bat.morning')}
        bind:value={wizard.battery.morningBufferPct}
        unit={UNIT.pct}
        min={0}
        max={100}
        hint={t('bat.morning_hint')}
        issues={bat('morning_buffer_pct')}
      />
      <label class="check">
        <input type="checkbox" bind:checked={wizard.battery.bridgeNextDay} />
        {t('bat.bridge')}
      </label>
      <p class="hint">{t('bat.bridge_hint')}</p>
    </fieldset>
  {/if}
</WizardShell>
