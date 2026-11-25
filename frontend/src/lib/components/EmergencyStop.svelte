<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import { t, tDynamic } from '../i18n/index.svelte';
  import { control } from '../stores/control.svelte';
  import { live } from '../stores/live.svelte';
  import Button from '../ui/Button.svelte';
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
  <Button
    variant="danger"
    disabled={busy}
    testid="estop-clear"
    onclick={() => {
      control.clearError('emergency');
      confirmClear = true;
    }}>{t('estop.clear')}</Button
  >
{:else}
  <!-- Phones show the short label; the accessible name is the full one and contains it. -->
  <Button
    variant="danger-fill"
    icon="stop"
    disabled={busy}
    testid="estop"
    onclick={() => void control.emergencyStop()}
    ><span class="long">{t('estop.button')}</span><span class="short" aria-hidden="true"
      >{t('estop.short')}</span
    ></Button
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
  .short {
    display: none;
  }
  @media (max-width: 599px) {
    .long {
      position: absolute;
      width: 1px;
      height: 1px;
      overflow: hidden;
      clip: rect(0, 0, 0, 0);
      white-space: nowrap;
    }
    .short {
      display: inline;
    }
  }
  .err {
    color: var(--sc-error);
    font-size: var(--sc-font-sm);
  }
</style>
