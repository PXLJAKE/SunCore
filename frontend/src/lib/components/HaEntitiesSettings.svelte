<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import { onMount } from 'svelte';
  import { api, surfaceApi } from '../api/client';
  import { describeError } from '../api/errors';
  import type { HaEntitiesStatus, MqttConfig } from '../api/types';
  import { t, tDynamic, type MessageKey } from '../i18n/index.svelte';
  import MqttBrokerSettings from './MqttBrokerSettings.svelte';

  const uid = $props.id();
  const STATE_LABEL: Record<HaEntitiesStatus['state'], MessageKey> = {
    disabled: 'ha.state.disabled',
    no_broker: 'ha.state.no_broker',
    connecting: 'ha.state.connecting',
    connected: 'ha.state.connected',
    error: 'ha.state.error',
  };

  let status = $state.raw<HaEntitiesStatus | null>(null);
  let config = $state.raw<MqttConfig | null>(null);
  let raise = $state(false);
  let loadError = $state<string | null>(null);
  let message = $state<{ kind: 'ok' | 'error'; text: string } | null>(null);
  let busy = $state(false);

  /** The stored section, or the defaults while nothing is stored (broker from the Supervisor). */
  const DEFAULTS: MqttConfig = {
    schema_version: 1,
    enabled: true,
    broker: 'supervisor',
    port: 1883,
    tls: false,
    discovery_prefix: 'homeassistant',
    allow_mode_raise: false,
  };

  async function load(): Promise<void> {
    try {
      const [s, c] = await Promise.all([
        surfaceApi.haEntities(),
        api.getSection<MqttConfig>('mqtt'),
      ]);
      status = s;
      config = c ?? DEFAULTS;
      raise = config.allow_mode_raise;
      loadError = null;
    } catch (e) {
      loadError = describeError(e).key;
    }
  }
  onMount(() => void load());

  /** The broker section was saved: take the server's copy and refresh the connection status. */
  function brokerSaved(saved: MqttConfig): void {
    config = saved;
    raise = saved.allow_mode_raise;
    void surfaceApi.haEntities().then(
      (s) => (status = s),
      () => undefined,
    );
  }

  async function republish(): Promise<void> {
    busy = true;
    message = null;
    try {
      const r = await surfaceApi.republish();
      message = r.republished
        ? { kind: 'ok', text: t('ha.republished') }
        : { kind: 'error', text: t('ha.republish_none') };
    } catch (e) {
      message = {
        kind: 'error',
        text: tDynamic(describeError(e).key, undefined, 'api.error.unknown'),
      };
    } finally {
      busy = false;
    }
  }

  async function saveRaise(): Promise<void> {
    if (!config) return;
    busy = true;
    message = null;
    try {
      config = await api.putSection<MqttConfig>('mqtt', { ...config, allow_mode_raise: raise });
      raise = config.allow_mode_raise;
      message = { kind: 'ok', text: t('ha.raise_saved') };
    } catch (e) {
      raise = config.allow_mode_raise;
      message = {
        kind: 'error',
        text: tDynamic(describeError(e).key, undefined, 'api.error.unknown'),
      };
    } finally {
      busy = false;
    }
  }
</script>

<section class="card stack" aria-labelledby="{uid}-ha" data-testid="ha-entities">
  <h3 id="{uid}-ha">{t('ha.title')}</h3>
  <p class="hint">{t('ha.intro')}</p>
  {#if loadError}
    <p class="notice error" role="alert">{tDynamic(loadError, undefined, 'api.error.unknown')}</p>
  {:else if status}
    <p data-testid="ha-status">
      <span class="chip-tag" class:warn={status.state !== 'connected'}
        >{t(STATE_LABEL[status.state])}</span
      >
      {#if status.state === 'connected'}
        {t('ha.count', { count: status.entity_count })}
      {/if}
    </p>
    {#if status.reason_key}
      <p class="hint" data-testid="ha-reason">
        {tDynamic(status.reason_key, undefined, 'mqtt.reason.unknown')}
      </p>
    {/if}
    {#if status.hint_key}
      <p class="notice" data-testid="ha-hint">
        {tDynamic(status.hint_key, undefined, 'mqtt.reason.unknown')}
      </p>
    {/if}
  {/if}

  <div class="row">
    <button
      type="button"
      class="btn secondary"
      disabled={busy || status?.state !== 'connected'}
      data-testid="ha-republish"
      onclick={() => void republish()}>{t('ha.republish')}</button
    >
  </div>
  <p class="hint">{t('ha.republish_hint')}</p>

  {#if config}
    <MqttBrokerSettings {config} onsaved={brokerSaved} />
  {/if}

  <label class="check">
    <input
      type="checkbox"
      bind:checked={raise}
      disabled={busy || config === null}
      data-testid="ha-allow-raise"
      onchange={() => void saveRaise()}
    />
    {t('ha.raise')}
  </label>
  <p class="hint">{t('ha.raise_hint')}</p>

  {#if message}
    <p
      class="notice {message.kind === 'ok' ? 'ok' : 'error'}"
      role="status"
      data-testid="ha-message"
    >
      {message.text}
    </p>
  {/if}
</section>

<style>
  .row {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }
</style>
