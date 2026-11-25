<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import { describeError, type FailedRequest } from '../api/errors';
  import { api } from '../api/client';
  import { t, tDynamic } from '../i18n/index.svelte';
  import { keepDevices, siteWithKeep, toggleKeep } from '../settings/phase6';
  import { app } from '../stores/app.svelte';
  import { wizard } from '../stores/wizard.svelte';
  import Button from '../ui/Button.svelte';
  import Card from '../ui/Card.svelte';
  import { issuesAt } from '../wizard/issues';
  import { siteFormFromSite } from '../wizard/siteForm';

  const site = $derived(wizard.saved.site);
  const devices = $derived(wizard.saved.devices?.devices ?? []);
  const stored = $derived(keepDevices(site));
  let edit = $state.raw<string[] | null>(null);
  let busy = $state(false);
  let failed = $state.raw<FailedRequest | null>(null);
  let saved = $state(false);

  const keep = $derived(edit ?? stored ?? []);
  const dirty = $derived(
    edit !== null &&
      stored !== null &&
      (edit.length !== stored.length || edit.some((x) => !stored.includes(x))),
  );
  const fieldIssues = $derived(issuesAt(failed?.issues ?? [], 'grid', 'backup', 'keep_devices'));

  function toggle(id: string, on: boolean): void {
    edit = toggleKeep(keep, id, on);
    saved = false;
  }

  async function save(): Promise<void> {
    if (!site) return;
    const body = siteWithKeep(site, keep);
    if (!body) return;
    busy = true;
    failed = null;
    saved = false;
    try {
      const result = await api.putSite(body);
      wizard.saved = { ...wizard.saved, site: result };
      wizard.site = siteFormFromSite(result);
      await app.reloadSite();
      edit = null;
      saved = true;
    } catch (e) {
      failed = describeError(e);
    } finally {
      busy = false;
    }
  }
</script>

{#if site && devices.length > 0}
  <Card title={t('p6.island.title')} level={4} testid="island-keep">
    <p class="hint">{t('p6.island.intro')}</p>
    {#if stored === null}
      <p class="notice info" data-testid="island-no-backup">{t('p6.island.no_backup')}</p>
    {:else}
      <fieldset class="keep">
        <legend class="label">{t('p6.island.legend')}</legend>
        {#each devices as d (d.id)}
          <label class="check">
            <input
              type="checkbox"
              checked={keep.includes(d.id)}
              data-testid="keep-{d.id}"
              onchange={(e) => toggle(d.id, e.currentTarget.checked)}
            />
            {d.name}
          </label>
        {/each}
      </fieldset>
      <p class="hint">{t('p6.island.hint')}</p>
      {#if failed}
        <div class="notice error" role="alert" data-testid="island-error">
          <p>{tDynamic(failed.key, undefined, 'api.error.unknown')}</p>
          <ul>
            {#each fieldIssues as issue, i (i)}
              <li>{tDynamic(issue.key, issue.args, 'api.validation.invalid')}</li>
            {/each}
          </ul>
        </div>
      {/if}
      {#if saved}
        <p class="notice ok" role="status" data-testid="island-saved">{t('p6.saved')}</p>
      {/if}
    {/if}
    {#snippet footer()}
      {#if stored !== null}
        <Button disabled={!dirty} {busy} testid="island-save" onclick={() => void save()}>
          {t('p6.save')}
        </Button>
      {/if}
    {/snippet}
  </Card>
{/if}

<style>
  .keep {
    display: grid;
    gap: var(--sc-space-1);
    margin: 0;
    padding: 0;
    border: 0;
    min-width: 0;
  }
  .label {
    padding: 0;
    font-size: var(--sc-font-sm);
    font-weight: var(--sc-weight-strong);
  }
</style>
