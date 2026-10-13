<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import { api } from '../../api/client';
  import { describeError } from '../../api/errors';
  import type {
    DeviceConfig,
    DeviceTemplate,
    TemplateSummary,
    ValidationIssue,
  } from '../../api/types';
  import DeviceEditor from '../../components/DeviceEditor.svelte';
  import TemplateGallery from '../../components/TemplateGallery.svelte';
  import WizardShell from '../../components/WizardShell.svelte';
  import { t, tDynamic } from '../../i18n/index.svelte';
  import { SaveState, wizard } from '../../stores/wizard.svelte';
  import {
    deviceFormFromConfig,
    deviceFromForm,
    newDeviceForm,
    newDeviceId,
    uniqueName,
    type DeviceForm,
  } from '../../wizard/deviceForm';
  import { scopeIssues } from '../../wizard/issues';
  import { orderedDevices } from '../../wizard/priorities';

  const save = new SaveState();

  type Mode = { type: 'list' } | { type: 'gallery' } | { type: 'edit' };
  let mode = $state<Mode>({ type: 'list' });
  let form = $state<DeviceForm | null>(null);
  let template = $state.raw<DeviceTemplate | null>(null);
  let saving = $state(false);
  let serverError = $state<string | null>(null);
  let serverIssues = $state.raw<ValidationIssue[]>([]);
  let notice = $state<string | null>(null);
  let removing = $state<string | null>(null);
  let loadError = $state<string | null>(null);

  const devices = $derived(orderedDevices(wizard.devices));

  async function fetchTemplate(id: string): Promise<DeviceTemplate | null> {
    try {
      const res = await api.template(id);
      return res.template.kind === 'device' ? res.template : null;
    } catch (e) {
      loadError = describeError(e).key;
      return null;
    }
  }

  async function pick(summary: TemplateSummary): Promise<void> {
    loadError = null;
    const tpl = await fetchTemplate(summary.id);
    if (!tpl) return;
    template = tpl;
    const name = uniqueName(
      tDynamic(tpl.name, undefined, 'reason.unknown'),
      wizard.devices.map((d) => d.name),
    );
    // New devices go to the end of the priority list.
    form = newDeviceForm(tpl, name, wizard.devices.length);
    serverError = null;
    serverIssues = [];
    mode = { type: 'edit' };
  }

  async function edit(d: DeviceConfig): Promise<void> {
    loadError = null;
    const tpl = await fetchTemplate(d.template_id);
    if (!tpl) return;
    template = tpl;
    form = deviceFormFromConfig(d, tpl);
    serverError = null;
    serverIssues = [];
    mode = { type: 'edit' };
  }

  function backToList(): void {
    mode = { type: 'list' };
    form = null;
    template = null;
  }

  async function saveDevice(): Promise<void> {
    if (!form || !template) return;
    saving = true;
    serverError = null;
    serverIssues = [];
    const taken = new Set(wizard.devices.map((d) => d.id));
    const id = form.id ?? newDeviceId(form.name, taken);
    const device = deviceFromForm(form, id, template);
    const isNew = form.id === null;
    const list = isNew
      ? [...wizard.devices, device]
      : wizard.devices.map((d) => (d.id === id ? device : d));
    const index = list.findIndex((d) => d.id === id);
    try {
      await wizard.saveDevices(list);
      notice = isNew
        ? t('devices.added', { name: device.name })
        : t('devices.updated', { name: device.name });
      backToList();
    } catch (e) {
      const failed = describeError(e);
      serverError = failed.key;
      serverIssues = scopeIssues(failed.issues, 'devices', index);
    } finally {
      saving = false;
    }
  }

  async function remove(d: DeviceConfig): Promise<void> {
    save.clear();
    try {
      await wizard.saveDevices(wizard.devices.filter((x) => x.id !== d.id));
      notice = t('devices.removed', { name: d.name });
    } catch (e) {
      save.error = describeError(e).key;
    }
    removing = null;
  }

  async function onsave(): Promise<void> {
    // Devices are stored as they are added; nothing left to write here.
  }
</script>

<WizardShell step="devices" intro={t('devices.intro')} {save} {onsave} skippable>
  {#if notice}
    <p class="notice ok" role="status" data-testid="devices-notice">{notice}</p>
  {/if}
  {#if loadError}
    <p class="notice error" role="alert">{tDynamic(loadError, undefined, 'api.error.unknown')}</p>
  {/if}

  {#if mode.type === 'gallery'}
    <TemplateGallery onpick={(tpl) => void pick(tpl)} oncancel={backToList} />
  {:else if mode.type === 'edit' && form && template}
    <DeviceEditor
      bind:form
      {template}
      issues={serverIssues}
      {saving}
      {serverError}
      onsave={() => void saveDevice()}
      oncancel={backToList}
    />
  {:else}
    <ul class="devices" data-testid="device-list">
      {#each devices as d (d.id)}
        <li class="card device">
          <div>
            <h3>{d.name}</h3>
            <p class="hint">
              {tDynamic(`template.${d.template_id}.name`, undefined, 'reason.unknown')} ·
              {tDynamic(`device.kind.${d.kind}`, undefined, 'device.kind.generic')} ·
              {t('devices.entities', { count: Object.keys(d.entities ?? {}).length })}
            </p>
          </div>
          <div class="row">
            <button type="button" class="btn secondary small" onclick={() => void edit(d)}
              >{t('action.edit')}<span class="sr-only"> {d.name}</span></button
            >
            {#if removing === d.id}
              <button type="button" class="btn danger small" onclick={() => void remove(d)}
                >{t('devices.remove_confirm')}<span class="sr-only"> {d.name}</span></button
              >
              <button type="button" class="btn secondary small" onclick={() => (removing = null)}
                >{t('action.cancel')}</button
              >
            {:else}
              <button type="button" class="btn secondary small" onclick={() => (removing = d.id)}
                >{t('action.remove')}<span class="sr-only"> {d.name}</span></button
              >
            {/if}
          </div>
        </li>
      {:else}
        <li class="hint" data-testid="no-devices">{t('devices.none')}</li>
      {/each}
    </ul>
    <div>
      <button
        type="button"
        class="btn"
        data-testid="add-device"
        onclick={() => {
          notice = null;
          mode = { type: 'gallery' };
        }}>{t('devices.add')}</button
      >
    </div>
    <p class="hint">{t('devices.shadow_note')}</p>
  {/if}
</WizardShell>

<style>
  .devices {
    display: grid;
    gap: 8px;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .device {
    display: flex;
    flex-wrap: wrap;
    gap: 8px 16px;
    justify-content: space-between;
    align-items: center;
  }
  .device h3 {
    margin: 0;
  }
  .device p {
    margin: 2px 0 0;
  }
  .row {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }
</style>
