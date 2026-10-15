<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import type {
    DeviceConfig,
    DeviceTemplate,
    EntityCandidate,
    PowerModelKind,
    ValidationIssue,
  } from '../api/types';
  import { t, tDynamic, type MessageKey } from '../i18n/index.svelte';
  import {
    controlRoleOf,
    powerModelOptions,
    roleByDeviceClass,
    OVERRIDE_HOURS_MAX,
    OVERRIDE_HOURS_MIN,
    POWER_UPDATE_MAX,
    POWER_UPDATE_MIN,
    preferredPowerKind,
    seemsConfiguredAlready,
    SLOW_POWER_SENSOR,
    SWITCH_ON_MODES,
    THERMAL_KINDS,
    validateDeviceForm,
    type DeviceForm,
    type DeviceFormError,
    type UnitInfo,
  } from '../wizard/deviceForm';
  import { emptyRef, toRef } from '../wizard/forms';
  import { loadCandidates } from '../stores/candidates';
  import { issuesAt } from '../wizard/issues';
  import FieldIssues from './FieldIssues.svelte';
  import NumberField from './NumberField.svelte';
  import RolePicker from './RolePicker.svelte';

  interface Props {
    form: DeviceForm;
    template: DeviceTemplate;
    /** The other configured devices (for the "configured already" hint). */
    others?: readonly DeviceConfig[];
    /** Issues of the server for this device (paths relative to the device). */
    issues: readonly ValidationIssue[];
    saving: boolean;
    serverError: string | null;
    onsave: () => void;
    oncancel: () => void;
  }
  let {
    form = $bindable(),
    template,
    others = [],
    issues,
    saving,
    serverError,
    onsave,
    oncancel,
  }: Props = $props();

  const uid = $props.id();
  const UNIT = { w: 'W', min: 'min', c: '°C', a: 'A', pct: '%', s: 's', h: 'h' } as const;
  const KIND_LABEL: Record<PowerModelKind, MessageKey> = {
    measured: 'device.power.measured',
    counter_derived: 'device.power.counter_derived',
    learned: 'device.power.learned',
    nominal: 'device.power.nominal',
  };
  const ERROR_TEXT = (e: DeviceFormError): string => {
    switch (e.key) {
      case 'name':
        return t('device.error.name');
      case 'role_missing':
        return t('device.error.role_missing', {
          role: template.roles[e.role]
            ? tDynamic(template.roles[e.role]!.description, undefined, 'reason.unknown')
            : e.role,
        });
      case 'nominal_missing':
        return t('device.error.nominal');
      case 'comfort_missing':
        return t('device.error.comfort_missing');
      case 'comfort_order':
        return t('device.error.comfort_order');
      case 'power_model_unavailable':
        return t('device.error.power_unavailable');
      case 'hvac_mode_missing':
        return t('device.error.hvac_mode_missing');
      case 'override_hours':
        return t('device.error.override_hours', {
          min: OVERRIDE_HOURS_MIN,
          max: OVERRIDE_HOURS_MAX,
        });
      case 'resume_time':
        return t('device.error.resume_time');
      case 'power_update':
        return t('device.error.power_update', { min: POWER_UPDATE_MIN, max: POWER_UPDATE_MAX });
    }
  };

  let attempted = $state(false);
  const errors = $derived(validateDeviceForm(form, template));
  const options = $derived(powerModelOptions(template, form.entities));
  const current = $derived(options.find((o) => o.kind === form.powerKind));
  const thermal = $derived(THERMAL_KINDS.includes(form.kind));
  const control = $derived(form.control);
  // Primary roles are mapped first; `same_device_as` roles only list entities of that device.
  const roles = $derived(
    Object.entries(template.roles).sort(
      ([, a], [, b]) => Number(a.same_device_as !== null) - Number(b.same_device_as !== null),
    ),
  );
  const at = (...p: (string | number)[]) => issuesAt(issues, ...p);
  const HVAC_LABEL: Record<(typeof SWITCH_ON_MODES)[number], MessageKey> = {
    heat: 'device.hvac.heat',
    cool: 'device.hvac.cool',
    heat_cool: 'device.hvac.heat_cool',
    auto: 'device.hvac.auto',
  };

  function track(role: string, c: EntityCandidate | null): void {
    const id = c?.device_id ?? null;
    if (form.deviceIds[role] !== id) form.deviceIds[role] = id;
    const name = c?.device_name ?? null;
    if (form.deviceNames[role] !== name) form.deviceNames[role] = name;
  }

  // Ranking hint for roles of another device (a power sensor of a second integration): the
  // device name of the control entity. Roles bound by `same_device_as` already follow its device.
  const controlRole = $derived(controlRoleOf(form.control));
  const hintName = (role: string, spec: DeviceTemplate['roles'][string]): string | undefined =>
    spec.same_device_as === null && role !== controlRole
      ? (form.deviceNames[controlRole] ?? undefined)
      : undefined;

  // All climate entities with their registry device, to compare device names of units.
  let units = $state.raw<ReadonlyMap<string, UnitInfo>>(new Map());
  $effect(() => {
    if (form.kind !== 'climate' || others.length === 0) return;
    let stale = false;
    loadCandidates({ role: 'climate', template: 'generic.climate' }).then(
      (list) => {
        if (stale) return;
        units = new Map(
          list.map((c) => [
            c.entity_id,
            { deviceId: c.device_id, deviceName: c.device_name, platform: c.platform },
          ]),
        );
      },
      () => {}, // a hint only: without the list there is simply no hint
    );
    return () => {
      stale = true;
    };
  });
  const duplicateUnit = $derived(seemsConfiguredAlready(form, others, units));

  // Until the user picks a power model, follow what is mapped: a power sensor means `measured`.
  // A model that the mapped roles no longer allow is always replaced.
  let kindTouched = $state(form.base !== null);
  $effect(() => {
    const opt = options.find((o) => o.kind === form.powerKind);
    const preferred = preferredPowerKind(template, form.entities);
    if (!kindTouched || (opt && !opt.available)) form.powerKind = preferred;
  });

  // A power sensor that is known to be slow (LG air conditioner through smartthinq_sensors:
  // 300 s) gets its update period preset, so the sensor is not called stale after a minute. Only
  // until the user touches the field; clearing it is allowed.
  let updateTouched = $state(false);
  const slowPreset = $derived(SLOW_POWER_SENSOR[form.templateId] ?? null);
  const powerSensorMapped = $derived(
    roleByDeviceClass(template, 'power').some(
      (r) => toRef(form.entities[r] ?? emptyRef()) !== null,
    ),
  );
  $effect(() => {
    if (updateTouched || slowPreset === null) return;
    if (
      form.powerKind === 'measured' &&
      powerSensorMapped &&
      form.powerRoles.powerUpdateS === null
    ) {
      form.powerRoles.powerUpdateS = slowPreset;
    }
  });

  function submit(): void {
    attempted = true;
    if (errors.length === 0) onsave();
  }
</script>

<section class="editor card stack" aria-labelledby="{uid}-title" data-testid="device-editor">
  <h3 id="{uid}-title">
    {form.id
      ? t('device.edit', { name: form.name })
      : t('device.new', { template: tDynamic(template.name, undefined, 'reason.unknown') })}
  </h3>

  <div class="field">
    <label for="{uid}-name">{t('device.name')}</label>
    <input id="{uid}-name" class="input" bind:value={form.name} autocomplete="off" maxlength="80" />
    <FieldIssues issues={at('name')} id="{uid}-name-err" />
  </div>

  <fieldset class="card">
    <legend>{t('device.entities')}</legend>
    {#each roles as [role, spec] (role)}
      <RolePicker
        label={tDynamic(spec.description, undefined, 'reason.unknown')}
        {role}
        template={template.id}
        deviceId={spec.same_device_as
          ? (form.deviceIds[spec.same_device_as] ?? undefined)
          : undefined}
        deviceName={hintName(role, spec)}
        bind:field={form.entities[role]!}
        optional={!spec.required}
        signed={spec.matcher.device_class === 'power'}
        oncandidate={(c) => track(role, c)}
        issues={at('entities', role)}
      />
    {/each}
    {#if duplicateUnit}
      <p class="notice warn" role="status" data-testid="duplicate-unit-hint">
        {t('device.duplicate_unit')}
      </p>
    {/if}
  </fieldset>

  <fieldset class="card">
    <legend>{t('device.power_model')}</legend>
    <p class="hint">{t('device.power_hint')}</p>
    <div class="field">
      <label for="{uid}-pm">{t('device.power_kind')}</label>
      <select
        id="{uid}-pm"
        class="input"
        bind:value={form.powerKind}
        onchange={() => (kindTouched = true)}
      >
        {#each options as o (o.kind)}
          <option value={o.kind} disabled={!o.available}>{t(KIND_LABEL[o.kind])}</option>
        {/each}
      </select>
      <FieldIssues issues={at('power_model')} id="{uid}-pm-err" />
    </div>
    {#if current?.needsNominal}
      <NumberField
        label={t('device.nominal')}
        bind:value={form.nominalW}
        unit={UNIT.w}
        min={0}
        hint={t('device.nominal_hint')}
        issues={at('power_model', 'nominal_w')}
      />
    {/if}
    {#if form.powerKind === 'measured' && powerSensorMapped}
      <NumberField
        label={t('device.power_update')}
        bind:value={form.powerRoles.powerUpdateS}
        unit={UNIT.s}
        min={POWER_UPDATE_MIN}
        max={POWER_UPDATE_MAX}
        step={1}
        hint={slowPreset !== null
          ? t('device.power_update_hint_slow')
          : t('device.power_update_hint')}
        issues={at('power_model', 'power_update_s')}
      />
    {/if}
  </fieldset>

  <fieldset class="card">
    <legend>{t('device.control')}</legend>
    <p class="hint">{t(`device.control.${control.kind}`)}</p>
    {#if control.kind === 'continuous'}
      <div class="cols">
        <NumberField
          label={t('device.control_min')}
          bind:value={control.min}
          unit={control.unit === 'A' ? UNIT.a : control.unit === '%' ? UNIT.pct : UNIT.w}
          min={0}
          issues={at('control', 'min')}
        />
        <NumberField
          label={t('device.control_max')}
          bind:value={control.max}
          unit={control.unit === 'A' ? UNIT.a : control.unit === '%' ? UNIT.pct : UNIT.w}
          min={0}
          issues={at('control', 'max')}
        />
      </div>
    {:else if control.kind === 'setpoint'}
      <NumberField
        label={t('device.shift_max')}
        bind:value={control.shift_max_c}
        unit={UNIT.c}
        min={0}
        max={10}
        hint={t('device.shift_hint')}
        issues={at('control', 'shift_max_c')}
      />
    {/if}
    {#if thermal}
      <div class="cols">
        <NumberField
          label={t('device.comfort_min')}
          bind:value={form.comfortMin}
          unit={UNIT.c}
          issues={at('comfort', 'min_c')}
        />
        <NumberField
          label={t('device.comfort_max')}
          bind:value={form.comfortMax}
          unit={UNIT.c}
          issues={at('comfort', 'max_c')}
        />
      </div>
      <p class="hint">{t('device.comfort_hint')}</p>
      <FieldIssues issues={at('comfort')} id="{uid}-comfort-err" />
    {/if}
  </fieldset>

  <fieldset class="card">
    <legend>{t('device.protection')}</legend>
    <p class="hint">{t('device.protection_hint')}</p>
    <div class="cols">
      <NumberField
        label={t('device.min_run')}
        bind:value={form.minRunMin}
        unit={UNIT.min}
        min={0}
        issues={at('min_run_s')}
      />
      <NumberField
        label={t('device.min_pause')}
        bind:value={form.minPauseMin}
        unit={UNIT.min}
        min={0}
        issues={at('min_pause_s')}
      />
    </div>
  </fieldset>

  {#if form.kind === 'climate'}
    <fieldset class="card" data-testid="switch-on">
      <legend>{t('device.switch_on')}</legend>
      <p class="hint">{t('device.switch_on_hint')}</p>
      <label class="check">
        <input type="checkbox" bind:checked={form.allowSwitchOn} data-testid="allow-switch-on" />
        {t('device.allow_switch_on')}
      </label>
      {#if form.allowSwitchOn}
        <div class="field">
          <label for="{uid}-hvac">{t('device.switch_on_mode')}</label>
          <select
            id="{uid}-hvac"
            class="input"
            data-testid="switch-on-mode"
            value={form.switchOnHvacMode ?? ''}
            aria-invalid={attempted && form.switchOnHvacMode === null ? 'true' : undefined}
            onchange={(e) =>
              (form.switchOnHvacMode =
                SWITCH_ON_MODES.find((m) => m === e.currentTarget.value) ?? null)}
          >
            <option value="" disabled>{t('device.switch_on_choose')}</option>
            {#each SWITCH_ON_MODES as m (m)}
              <option value={m}>{t(HVAC_LABEL[m])}</option>
            {/each}
          </select>
          <FieldIssues issues={at('switch_on_hvac_mode')} id="{uid}-hvac-err" />
        </div>
      {/if}
      <FieldIssues issues={at('allow_switch_on')} id="{uid}-allow-err" />
    </fieldset>
  {/if}

  <fieldset class="card" data-testid="override-policy">
    <legend>{t('device.override')}</legend>
    <p class="hint">{t('device.override_hint')}</p>
    <div class="radios" role="radiogroup" aria-label={t('device.override')}>
      <label class="radio">
        <input type="radio" name="{uid}-ovr" value="next_day" bind:group={form.overrideKind} />
        {t('device.override_next_day')}
      </label>
      <label class="radio">
        <input type="radio" name="{uid}-ovr" value="hours" bind:group={form.overrideKind} />
        {t('device.override_hours')}
      </label>
    </div>
    {#if form.overrideKind === 'next_day'}
      <div class="field">
        <label for="{uid}-resume">{t('device.override_time')}</label>
        <input
          id="{uid}-resume"
          class="input time"
          type="time"
          bind:value={form.overrideResumeTime}
        />
        <FieldIssues issues={at('override_policy', 'resume_time')} id="{uid}-resume-err" />
      </div>
    {:else}
      <NumberField
        label={t('device.override_hours_label')}
        bind:value={form.overrideHours}
        unit={UNIT.h}
        min={OVERRIDE_HOURS_MIN}
        max={OVERRIDE_HOURS_MAX}
        step={1}
        issues={at('override_policy', 'hours')}
      />
    {/if}
    <FieldIssues issues={at('override_policy')} id="{uid}-ovr-err" />
  </fieldset>

  {#if (template.unverified ?? []).length > 0 && template.tested_with.length === 0}
    <p class="notice warn">{t('template.untested_long')}</p>
  {/if}

  {#if attempted && errors.length > 0}
    <div class="notice error" role="alert" data-testid="device-errors">
      <ul>
        {#each errors as e, i (i)}<li>{ERROR_TEXT(e)}</li>{/each}
      </ul>
    </div>
  {/if}
  {#if serverError}
    <div class="notice error" role="alert" data-testid="device-server-error">
      <p>{tDynamic(serverError, undefined, 'api.error.unknown')}</p>
      {#if issues.length > 0}
        <ul>
          {#each issues as issue, i (i)}
            <li>{tDynamic(issue.key, issue.args, 'api.validation.invalid')}</li>
          {/each}
        </ul>
      {/if}
    </div>
  {/if}

  <div class="actions">
    <button type="button" class="btn" disabled={saving} data-testid="device-save" onclick={submit}
      >{saving ? t('action.saving') : t('device.save')}</button
    >
    <button type="button" class="btn secondary" onclick={oncancel}>{t('action.cancel')}</button>
  </div>
</section>

<style>
  .time {
    max-width: 10rem;
  }
  .actions {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }
</style>
