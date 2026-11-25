<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import { api } from '../api/client';
  import { describeError, type FailedRequest } from '../api/errors';
  import type { MqttConfig } from '../api/types';
  import { t, tDynamic } from '../i18n/index.svelte';
  import {
    hasStoredPassword,
    MQTT_PORT_MAX,
    MQTT_PORT_MIN,
    mqttDirty,
    mqttFormFromConfig,
    mqttFromForm,
    type MqttBrokerForm,
  } from '../settings/phase6';
  import Button from '../ui/Button.svelte';
  import FormRow from '../ui/FormRow.svelte';
  import { issuesAt } from '../wizard/issues';
  import { num } from '../wizard/forms';

  interface Props {
    /** The stored section (the password is the mask or none); owned by the parent. */
    config: MqttConfig;
    /** Called with the section the server returned after a save. */
    onsaved: (config: MqttConfig) => void;
  }
  let { config, onsaved }: Props = $props();

  const uid = $props.id();
  // Edits start from the stored section and are not reset by a reload of the same values.
  // svelte-ignore state_referenced_locally
  let form = $state<MqttBrokerForm>(mqttFormFromConfig(config));
  let busy = $state(false);
  let failed = $state.raw<FailedRequest | null>(null);
  let saved = $state(false);

  const hadPassword = $derived(hasStoredPassword(config));
  const dirty = $derived(mqttDirty(form, config));
  const manual = $derived(form.broker === 'manual');

  function errorOf(field: string): string | null {
    const issues = issuesAt(failed?.issues ?? [], field);
    if (issues.length === 0) return null;
    return issues.map((i) => tDynamic(i.key, i.args, 'api.validation.invalid')).join(' ');
  }
  /** Issues of the section as a whole (a model-level check has no field in its path). */
  const general = $derived(
    (failed?.issues ?? []).filter((i) => {
      const loc = i.loc.filter((p) => p !== 'body');
      return loc.length === 0;
    }),
  );

  function touch(): void {
    saved = false;
  }

  async function save(): Promise<void> {
    busy = true;
    failed = null;
    saved = false;
    try {
      const result = await api.putSection<MqttConfig>(
        'mqtt',
        mqttFromForm(form, config, hadPassword),
      );
      form = mqttFormFromConfig(result);
      onsaved(result);
      saved = true;
    } catch (e) {
      failed = describeError(e);
    } finally {
      busy = false;
    }
  }
</script>

<div class="broker" data-testid="mqtt-broker">
  <h4 id="{uid}-title" class="sub">{t('p6.mqtt.title')}</h4>
  <p class="hint">{t('p6.mqtt.intro')}</p>

  <div class="radios" role="radiogroup" aria-labelledby="{uid}-title">
    <label class="radio">
      <input
        type="radio"
        name="{uid}-broker"
        value="supervisor"
        bind:group={form.broker}
        data-testid="mqtt-broker-supervisor"
        onchange={touch}
      />
      {t('p6.mqtt.supervisor')}
    </label>
    <label class="radio">
      <input
        type="radio"
        name="{uid}-broker"
        value="manual"
        bind:group={form.broker}
        data-testid="mqtt-broker-manual"
        onchange={touch}
      />
      {t('p6.mqtt.manual')}
    </label>
  </div>

  {#if manual}
    <FormRow label={t('p6.mqtt.host')} hint={t('p6.mqtt.host_hint')} error={errorOf('host')}>
      {#snippet children({ id, describedby, invalid })}
        <input
          {id}
          class="input"
          type="text"
          autocomplete="off"
          spellcheck="false"
          bind:value={form.host}
          aria-describedby={describedby}
          aria-invalid={invalid ? 'true' : undefined}
          data-testid="mqtt-host"
          oninput={touch}
        />
      {/snippet}
    </FormRow>
    <FormRow
      label={t('p6.mqtt.port')}
      hint={t('p6.mqtt.port_hint', { min: MQTT_PORT_MIN, max: MQTT_PORT_MAX })}
      error={errorOf('port')}
    >
      {#snippet children({ id, describedby, invalid })}
        <input
          {id}
          class="input narrow"
          type="number"
          inputmode="numeric"
          min={MQTT_PORT_MIN}
          max={MQTT_PORT_MAX}
          step="1"
          value={form.port ?? ''}
          aria-describedby={describedby}
          aria-invalid={invalid ? 'true' : undefined}
          data-testid="mqtt-port"
          oninput={(e) => {
            form.port = num(e.currentTarget.valueAsNumber);
            touch();
          }}
        />
      {/snippet}
    </FormRow>
    <label class="check">
      <input type="checkbox" bind:checked={form.tls} data-testid="mqtt-tls" onchange={touch} />
      {t('p6.mqtt.tls')}
    </label>
    <FormRow label={t('p6.mqtt.username')} error={errorOf('username')}>
      {#snippet children({ id, describedby, invalid })}
        <input
          {id}
          class="input"
          type="text"
          autocomplete="off"
          spellcheck="false"
          bind:value={form.username}
          aria-describedby={describedby}
          aria-invalid={invalid ? 'true' : undefined}
          data-testid="mqtt-username"
          oninput={touch}
        />
      {/snippet}
    </FormRow>
    <FormRow
      label={t('p6.mqtt.password')}
      hint={hadPassword && !form.clearPassword ? t('p6.mqtt.password_stored') : undefined}
      error={errorOf('password')}
    >
      {#snippet children({ id, describedby, invalid })}
        <input
          {id}
          class="input"
          type="password"
          autocomplete="new-password"
          bind:value={form.password}
          disabled={form.clearPassword}
          placeholder={hadPassword && !form.clearPassword ? '••••••••' : ''}
          aria-describedby={describedby}
          aria-invalid={invalid ? 'true' : undefined}
          data-testid="mqtt-password"
          oninput={touch}
        />
      {/snippet}
    </FormRow>
    {#if hadPassword}
      <label class="check">
        <input
          type="checkbox"
          bind:checked={form.clearPassword}
          data-testid="mqtt-clear-password"
          onchange={() => {
            form.password = '';
            touch();
          }}
        />
        {t('p6.mqtt.password_clear')}
      </label>
    {/if}
  {/if}

  {#if failed}
    <div class="notice error" role="alert" data-testid="mqtt-error">
      <p>{tDynamic(failed.key, undefined, 'api.error.unknown')}</p>
      <ul>
        {#each general as issue, i (i)}
          <li>{tDynamic(issue.key, issue.args, 'api.validation.invalid')}</li>
        {/each}
      </ul>
    </div>
  {/if}
  {#if saved}
    <p class="notice ok" role="status" data-testid="mqtt-saved">{t('p6.saved')}</p>
  {/if}
  <div class="row">
    <Button disabled={!dirty} {busy} testid="mqtt-save" onclick={() => void save()}>
      {t('p6.save')}
    </Button>
  </div>
</div>

<style>
  .broker {
    display: grid;
    gap: var(--sc-space-3);
  }
  .sub {
    margin: 0;
    font-size: var(--sc-font-md);
    font-weight: var(--sc-weight-strong);
  }
  .radios {
    display: flex;
    flex-wrap: wrap;
    gap: var(--sc-space-1) var(--sc-space-5);
  }
  .narrow {
    max-width: 10rem;
  }
  .row {
    display: flex;
    gap: var(--sc-space-2);
  }
</style>
