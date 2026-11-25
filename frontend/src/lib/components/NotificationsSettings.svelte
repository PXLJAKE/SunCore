<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import { onMount } from 'svelte';
  import { surfaceApi } from '../api/client';
  import { describeError } from '../api/errors';
  import type { NotificationsConfig, NotificationsStatus, NotificationTest } from '../api/types';
  import { t, tDynamic, type MessageKey } from '../i18n/index.svelte';
  import { addTarget, CATEGORIES, removeTarget, targetKey, type Category } from '../notify/present';

  const uid = $props.id();
  const CATEGORY_LABEL: Record<Category, MessageKey> = {
    error: 'notify.cat.error',
    safety: 'notify.cat.safety',
    proposal: 'notify.cat.proposal',
    program: 'notify.cat.program',
    ev_target: 'notify.cat.ev_target',
    cheap_window: 'notify.cat.cheap_window',
    daily_summary: 'notify.cat.daily_summary',
  };

  let config = $state<NotificationsConfig | null>(null);
  let status = $state.raw<NotificationsStatus | null>(null);
  let loadError = $state<string | null>(null);
  let message = $state<{ kind: 'ok' | 'error'; text: string } | null>(null);
  let testResult = $state.raw<NotificationTest | null>(null);
  let busy = $state(false);
  let entityChoice = $state('');
  let serviceName = $state('');

  async function load(): Promise<void> {
    try {
      const [c, s] = await Promise.all([
        surfaceApi.notifications(),
        surfaceApi.notificationsStatus(),
      ]);
      config = c;
      status = s;
      loadError = null;
    } catch (e) {
      loadError = describeError(e).key;
    }
  }
  onMount(() => void load());

  const freeEntities = $derived(
    (status?.entity_targets ?? []).filter(
      (id) => !(config?.targets ?? []).some((x) => x.kind === 'entity' && x.id === id),
    ),
  );

  function add(kind: 'entity' | 'service', id: string): void {
    if (!config) return;
    config = { ...config, targets: addTarget(config.targets, kind, id) };
    if (kind === 'entity') entityChoice = '';
    else serviceName = '';
  }

  async function save(): Promise<void> {
    if (!config) return;
    busy = true;
    message = null;
    try {
      config = await surfaceApi.putNotifications(config);
      status = await surfaceApi.notificationsStatus();
      message = { kind: 'ok', text: t('notify.saved') };
    } catch (e) {
      const failed = describeError(e);
      message = {
        kind: 'error',
        text: tDynamic(
          failed.issues[0]?.key ?? failed.key,
          failed.issues[0]?.args,
          'api.error.unknown',
        ),
      };
    } finally {
      busy = false;
    }
  }

  async function test(): Promise<void> {
    busy = true;
    message = null;
    try {
      testResult = await surfaceApi.notificationsTest();
    } catch (e) {
      testResult = null;
      message = {
        kind: 'error',
        text: tDynamic(describeError(e).key, undefined, 'api.error.unknown'),
      };
    } finally {
      busy = false;
    }
  }
</script>

<section class="card stack" aria-labelledby="{uid}-nt" data-testid="notifications">
  <h3 id="{uid}-nt">{t('notify.title')}</h3>
  <p class="hint">{t('notify.intro')}</p>
  {#if loadError}
    <p class="notice error" role="alert">{tDynamic(loadError, undefined, 'api.error.unknown')}</p>
  {/if}

  {#if config}
    {#if status}
      <p data-testid="notify-status">
        <span class="chip-tag" class:warn={status.actions === 'refused'}
          >{t(`notify.actions.${status.actions}`)}</span
        >
        {t('notify.counts', { targets: status.push_targets, today: status.pushed_today })}
      </p>
      {#if status.reason_key}
        <p class="hint" data-testid="notify-reason">
          {tDynamic(status.reason_key, undefined, 'reason.unknown')}
        </p>
      {/if}
    {/if}

    <label class="check">
      <input type="checkbox" bind:checked={config.persistent} data-testid="notify-persistent" />
      {t('notify.persistent')}
    </label>

    <fieldset class="card">
      <legend>{t('notify.categories')}</legend>
      {#each CATEGORIES as c (c)}
        <label class="check">
          <input type="checkbox" bind:checked={config.categories[c]} data-testid="notify-cat-{c}" />
          {t(CATEGORY_LABEL[c])}
        </label>
      {/each}
    </fieldset>

    <fieldset class="card">
      <legend>{t('notify.targets')}</legend>
      <p class="hint">{t('notify.targets_hint')}</p>
      {#if config.targets.length === 0}
        <p class="hint" data-testid="notify-no-targets">{t('notify.no_targets')}</p>
      {:else}
        <ul class="targets" data-testid="notify-targets">
          {#each config.targets as target (targetKey(target))}
            <li>
              <span class="id">{target.id}</span>
              <span class="chip-tag">{t(`notify.kind.${target.kind}`)}</span>
              <button
                type="button"
                class="btn small secondary"
                onclick={() => config && (config.targets = removeTarget(config.targets, target))}
                >{t('action.remove')}<span class="sr-only"> {target.id}</span></button
              >
            </li>
          {/each}
        </ul>
      {/if}
      {#if freeEntities.length > 0}
        <div class="field">
          <label for="{uid}-ent">{t('notify.add_entity')}</label>
          <div class="row">
            <select id="{uid}-ent" class="input" bind:value={entityChoice}>
              <option value="">{t('notify.choose')}</option>
              {#each freeEntities as id (id)}<option value={id}>{id}</option>{/each}
            </select>
            <button
              type="button"
              class="btn small"
              disabled={entityChoice === ''}
              onclick={() => add('entity', entityChoice)}>{t('notify.add')}</button
            >
          </div>
        </div>
      {/if}
      <div class="field">
        <label for="{uid}-svc">{t('notify.add_service')}</label>
        <div class="row">
          <input
            id="{uid}-svc"
            class="input"
            bind:value={serviceName}
            placeholder={t('notify.service_placeholder')}
            autocomplete="off"
            data-testid="notify-service"
          />
          <button
            type="button"
            class="btn small"
            disabled={serviceName.trim() === ''}
            data-testid="notify-add-service"
            onclick={() => add('service', serviceName.trim())}>{t('notify.add')}</button
          >
        </div>
        <p class="hint">{t('notify.service_hint')}</p>
      </div>
    </fieldset>

    <fieldset class="card">
      <legend>{t('notify.quiet')}</legend>
      <label class="check">
        <input
          type="checkbox"
          bind:checked={config.quiet_hours.enabled}
          data-testid="notify-quiet"
        />
        {t('notify.quiet_enable')}
      </label>
      {#if config.quiet_hours.enabled}
        <div class="row">
          <div class="field">
            <label for="{uid}-qs">{t('notify.quiet_start')}</label>
            <input
              id="{uid}-qs"
              class="input time"
              type="time"
              bind:value={config.quiet_hours.start}
            />
          </div>
          <div class="field">
            <label for="{uid}-qe">{t('notify.quiet_end')}</label>
            <input
              id="{uid}-qe"
              class="input time"
              type="time"
              bind:value={config.quiet_hours.end}
            />
          </div>
        </div>
      {/if}
      <p class="hint">{t('notify.quiet_hint')}</p>
    </fieldset>

    <div class="field">
      <label for="{uid}-cap">{t('notify.cap')}</label>
      <input
        id="{uid}-cap"
        class="input cap"
        type="number"
        min="0"
        max="200"
        step="1"
        bind:value={config.daily_push_cap}
      />
      <p class="hint">{t('notify.cap_hint')}</p>
    </div>

    <div class="row">
      <button
        type="button"
        class="btn"
        disabled={busy}
        data-testid="notify-save"
        onclick={() => void save()}>{t('notify.save')}</button
      >
      <button
        type="button"
        class="btn secondary"
        disabled={busy}
        data-testid="notify-test"
        onclick={() => void test()}>{t('notify.test')}</button
      >
    </div>
    {#if testResult}
      <p
        class="notice {testResult.reason_key ? 'warn' : 'ok'}"
        role="status"
        data-testid="notify-test-result"
      >
        {testResult.reason_key
          ? tDynamic(testResult.reason_key, undefined, 'reason.unknown')
          : t('notify.test_result', { sent: testResult.sent, failed: testResult.failed })}
      </p>
    {/if}
    {#if message}
      <p class="notice {message.kind}" role="status" data-testid="notify-message">{message.text}</p>
    {/if}
  {/if}
</section>

<style>
  .row {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    align-items: end;
  }
  .row .input {
    flex: 1 1 10rem;
    width: auto;
  }
  .time,
  .cap {
    max-width: 10rem;
  }
  .targets {
    list-style: none;
    margin: 0;
    padding: 0;
    display: grid;
    gap: 6px;
  }
  .targets li {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
  }
  .id {
    overflow-wrap: anywhere;
    font-family: var(--code-font-family, monospace);
  }
</style>
