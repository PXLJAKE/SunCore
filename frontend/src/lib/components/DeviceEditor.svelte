<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import type {
    DeviceTemplate,
    EntityCandidate,
    PowerModelKind,
    ValidationIssue,
  } from '../api/types';
  import { t, tDynamic, type MessageKey } from '../i18n/index.svelte';
  import {
    powerModelOptions,
    preferredPowerKind,
    THERMAL_KINDS,
    validateDeviceForm,
    type DeviceForm,
    type DeviceFormError,
  } from '../wizard/deviceForm';
  import { issuesAt } from '../wizard/issues';
  import FieldIssues from './FieldIssues.svelte';
  import NumberField from './NumberField.svelte';
  import RolePicker from './RolePicker.svelte';

  interface Props {
    form: DeviceForm;
    template: DeviceTemplate;
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
    issues,
    saving,
    serverError,
    onsave,
    oncancel,
  }: Props = $props();

  const uid = $props.id();
  const UNIT = { w: 'W', min: 'min', c: '°C', a: 'A', pct: '%' } as const;
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
        return t('device.error.role_missing', { role: e.role });
      case 'nominal_missing':
        return t('device.error.nominal');
      case 'comfort_missing':
        return t('device.error.comfort_missing');
      case 'comfort_order':
        return t('device.error.comfort_order');
      case 'power_model_unavailable':
        return t('device.error.power_unavailable');
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

  function track(role: string, c: EntityCandidate | null): void {
    const id = c?.device_id ?? null;
    if (form.deviceIds[role] !== id) form.deviceIds[role] = id;
  }

  // Until the user picks a power model, follow what is mapped: a power sensor means `measured`.
  // A model that the mapped roles no longer allow is always replaced.
  let kindTouched = $state(form.base !== null);
  $effect(() => {
    const opt = options.find((o) => o.kind === form.powerKind);
    const preferred = preferredPowerKind(template, form.entities);
    if (!kindTouched || (opt && !opt.available)) form.powerKind = preferred;
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
        bind:field={form.entities[role]!}
        optional={!spec.required}
        signed={spec.matcher.device_class === 'power'}
        oncandidate={(c) => track(role, c)}
        issues={at('entities', role)}
      />
    {/each}
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
  .actions {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }
</style>
