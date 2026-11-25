<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import { describeError, type FailedRequest } from '../api/errors';
  import { t, tDynamic } from '../i18n/index.svelte';
  import {
    applyDeviceGridEdits,
    deviceGridChanged,
    deviceGridEdit,
    SHED_RANK_MAX,
    SHED_RANK_MIN,
    type DeviceGridEdit,
  } from '../settings/phase6';
  import { wizard } from '../stores/wizard.svelte';
  import Button from '../ui/Button.svelte';
  import Card from '../ui/Card.svelte';
  import FormRow from '../ui/FormRow.svelte';
  import { issuesAt } from '../wizard/issues';
  import { num } from '../wizard/forms';

  const devices = $derived(wizard.saved.devices?.devices ?? []);
  /** Edits not saved yet, by device id; a device without an entry shows its stored values. */
  let edits = $state<Record<string, DeviceGridEdit>>({});
  let busy = $state(false);
  let failed = $state.raw<FailedRequest | null>(null);
  let saved = $state(false);

  const view = (id: string): DeviceGridEdit =>
    edits[id] ?? deviceGridEdit(devices.find((d) => d.id === id)!);
  const dirty = $derived(devices.some((d) => d.id in edits && deviceGridChanged(d, edits[d.id]!)));

  function patch(id: string, change: Partial<DeviceGridEdit>): void {
    edits = { ...edits, [id]: { ...view(id), ...change } };
    saved = false;
  }

  /** Validation messages of one device field (`devices.<index>.<field>`). */
  function errorOf(index: number, field: string): string | null {
    const issues = issuesAt(failed?.issues ?? [], 'devices', index, field);
    if (issues.length === 0) return null;
    return issues.map((i) => tDynamic(i.key, i.args, 'api.validation.invalid')).join(' ');
  }

  async function save(): Promise<void> {
    busy = true;
    failed = null;
    saved = false;
    try {
      await wizard.saveDevices((latest) => applyDeviceGridEdits(latest, edits));
      edits = {};
      saved = true;
    } catch (e) {
      failed = describeError(e);
    } finally {
      busy = false;
    }
  }
</script>

{#if devices.length > 0}
  <Card title={t('p6.devices.title')} level={4} testid="device-grid-settings">
    <p class="hint">{t('p6.devices.intro')}</p>
    <p class="hint">{t('p6.para14a.hint')}</p>
    <p class="hint">{t('p6.shed_rank.hint', { min: SHED_RANK_MIN, max: SHED_RANK_MAX })}</p>
    <ul class="devs">
      {#each devices as d, i (d.id)}
        {@const v = view(d.id)}
        <li data-testid="device-grid-{d.id}">
          <fieldset>
            <legend class="name">{d.name}</legend>
            <label class="check">
              <input
                type="checkbox"
                checked={v.para14a}
                data-testid="para14a-{d.id}"
                onchange={(e) => patch(d.id, { para14a: e.currentTarget.checked })}
              />
              {t('p6.para14a.label')}
            </label>
            {#if errorOf(i, 'para14a')}
              <p class="err" role="alert">{errorOf(i, 'para14a')}</p>
            {/if}
            <FormRow label={t('p6.shed_rank.label')} error={errorOf(i, 'shed_rank')}>
              {#snippet children({ id, describedby, invalid })}
                <input
                  {id}
                  class="input narrow"
                  type="number"
                  inputmode="numeric"
                  min={SHED_RANK_MIN}
                  max={SHED_RANK_MAX}
                  step="1"
                  value={v.shedRank ?? ''}
                  aria-describedby={describedby}
                  aria-invalid={invalid ? 'true' : undefined}
                  data-testid="shed-rank-{d.id}"
                  oninput={(e) => patch(d.id, { shedRank: num(e.currentTarget.valueAsNumber) })}
                />
              {/snippet}
            </FormRow>
          </fieldset>
        </li>
      {/each}
    </ul>

    {#if failed}
      <p class="notice error" role="alert" data-testid="device-grid-error">
        {tDynamic(failed.key, undefined, 'api.error.unknown')}
      </p>
    {/if}
    {#if saved}
      <p class="notice ok" role="status" data-testid="device-grid-saved">{t('p6.saved')}</p>
    {/if}
    {#snippet footer()}
      <Button disabled={!dirty} {busy} testid="device-grid-save" onclick={() => void save()}>
        {t('p6.save')}
      </Button>
    {/snippet}
  </Card>
{/if}

<style>
  .devs {
    margin: 0;
    padding: 0;
    list-style: none;
    display: grid;
    gap: var(--sc-space-4);
  }
  fieldset {
    display: grid;
    gap: var(--sc-space-2);
    margin: 0;
    padding: 0;
    border: 0;
    min-width: 0;
  }
  .devs > li {
    display: grid;
    gap: var(--sc-space-2);
    padding-bottom: var(--sc-space-4);
  }
  .devs > li + li {
    border-top: 1px solid var(--sc-outline);
    padding-top: var(--sc-space-4);
  }
  .name {
    margin: 0;
    font-size: var(--sc-font-md);
    font-weight: var(--sc-weight-strong);
  }
  .narrow {
    max-width: 10rem;
  }
  .err {
    margin: 0;
    color: var(--sc-error);
    font-size: var(--sc-font-sm);
  }
</style>
