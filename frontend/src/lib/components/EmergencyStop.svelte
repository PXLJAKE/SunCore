<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import { t, tDynamic } from '../i18n/index.svelte';
  import { control } from '../stores/control.svelte';
  import { live } from '../stores/live.svelte';
  import ConfirmDialog from './ConfirmDialog.svelte';

  const stopped = $derived(live.control?.emergency_stop ?? false);
  const busy = $derived(control.busy['emergency'] ?? false);
  const error = $derived(control.errors['emergency'] ?? null);
  let confirmClear = $state(false);

  async function clear(): Promise<void> {
    const ok = await control.clearEmergencyStop();
    if (ok) confirmClear = false;
  }
</script>

{#if stopped}
  <button
    type="button"
    class="btn resume"
    disabled={busy}
    data-testid="estop-clear"
    onclick={() => {
      control.clearError('emergency');
      confirmClear = true;
    }}>{t('estop.clear')}</button
  >
{:else}
  <button
    type="button"
    class="btn stop"
    disabled={busy}
    data-testid="estop"
    onclick={() => void control.emergencyStop()}>{t('estop.button')}</button
  >
{/if}

{#if error && !confirmClear}
  <span class="err" role="alert" data-testid="estop-error"
    >{tDynamic(error, undefined, 'api.error.unknown')}</span
  >
{/if}

<ConfirmDialog
  open={confirmClear}
  title={t('estop.clear_title')}
  confirmLabel={t('estop.clear_confirm')}
  {busy}
  onconfirm={() => void clear()}
  oncancel={() => (confirmClear = false)}
  testid="estop-dialog"
>
  <p>{t('estop.clear_text')}</p>
  {#if error}<p class="err" role="alert">{tDynamic(error, undefined, 'api.error.unknown')}</p>{/if}
</ConfirmDialog>

<style>
  .stop {
    background: var(--error-color);
    border-color: var(--error-color);
    color: var(--card-background-color);
    font-weight: 700;
  }
  .resume {
    background: transparent;
    border-color: var(--error-color);
    color: var(--error-color);
    font-weight: 700;
  }
  .err {
    color: var(--error-color);
    font-size: 0.875rem;
  }
</style>
