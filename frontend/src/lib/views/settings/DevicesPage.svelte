<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import DeviceGridSettings from '../../components/DeviceGridSettings.svelte';
  import IslandKeepSettings from '../../components/IslandKeepSettings.svelte';
  import { t, tDynamic } from '../../i18n/index.svelte';
  import { pageRows } from '../../settings/summary';
  import { wizard } from '../../stores/wizard.svelte';
  import StepRows from './StepRows.svelte';

  const rows = $derived(pageRows('devices', wizard.saved));
  const devices = $derived(wizard.saved.devices?.devices ?? []);
</script>

<StepRows {rows} />

<!-- What is configured, read-only; the devices step edits it (gallery, per-device fields). -->
{#if devices.length > 0}
  <section class="card list" aria-labelledby="dev-list-title" data-testid="settings-devices">
    <h4 id="dev-list-title" class="kicker">{t('settings.devices.configured')}</h4>
    <ul>
      {#each devices as d (d.id)}
        <li>
          <span class="name">{d.name}</span>
          <span class="hint">
            {tDynamic(`template.${d.template_id}.name`, undefined, 'reason.unknown')} ·
            {tDynamic(`device.kind.${d.kind}`, undefined, 'device.kind.generic')}
          </span>
        </li>
      {/each}
    </ul>
  </section>
{/if}
<DeviceGridSettings />
<IslandKeepSettings />
<p class="hint">{t('devices.shadow_note')}</p>

<style>
  .list {
    display: grid;
    gap: var(--sc-space-2);
  }
  ul {
    margin: 0;
    padding: 0;
    list-style: none;
  }
  li {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    justify-content: space-between;
    gap: 0 var(--sc-space-3);
    padding: var(--sc-space-2) 0;
  }
  li + li {
    border-top: 1px solid var(--sc-outline);
  }
  .name {
    font-weight: var(--sc-weight-strong);
  }
</style>
