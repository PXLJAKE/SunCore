<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import { t, tDynamic, type MessageKey } from '../i18n/index.svelte';
  import { control } from '../stores/control.svelte';
  import ReasonList from './ReasonList.svelte';

  interface Props {
    names: ReadonlyMap<string, string>;
  }
  let { names }: Props = $props();

  const KIND: Record<string, MessageKey> = {
    accepted: 'decision.kind.accepted',
    rejected: 'decision.kind.rejected',
    shadow: 'decision.kind.shadow',
    proposed: 'decision.kind.proposed',
    held: 'decision.kind.held',
    failed: 'decision.kind.failed',
  };
  const d = $derived(control.lastConfirm);
  const bad = $derived(d !== null && (d.kind === 'rejected' || d.kind === 'failed'));
</script>

{#if d}
  <div
    class="notice"
    class:error={bad}
    class:ok={!bad}
    role="status"
    data-testid="confirm-result"
    data-kind={d.kind}
  >
    <p>
      {t('proposals.result', {
        device: names.get(d.intent.asset_id) ?? d.intent.asset_id,
        outcome: tDynamic(KIND[d.kind] ?? 'reason.unknown', undefined),
      })}
    </p>
    {#if d.reasons.length > 0}<ReasonList reasons={d.reasons} />{/if}
    <button type="button" class="btn small secondary" onclick={() => (control.lastConfirm = null)}
      >{t('action.close')}</button
    >
  </div>
{/if}
