<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import type { Decision } from '../api/types';
  import { describeAction } from '../control/actions';
  import { clock, secondsLeft } from '../control/present';
  import { t, tDynamic, tPower } from '../i18n/index.svelte';
  import { control } from '../stores/control.svelte';
  import ReasonList from './ReasonList.svelte';

  interface Props {
    proposals: readonly Decision[];
    names: ReadonlyMap<string, string>;
  }
  let { proposals, names }: Props = $props();

  // One timer for the whole list, only while there is something to count down.
  let now = $state(Date.now());
  $effect(() => {
    if (proposals.length === 0) return;
    now = Date.now();
    const id = setInterval(() => (now = Date.now()), 1000);
    return () => clearInterval(id);
  });
</script>

{#if proposals.length > 0}
  <section class="card proposals" aria-labelledby="proposals-title" data-testid="proposals">
    <h3 id="proposals-title">{t('proposals.title')}</h3>
    <p class="hint">{t('proposals.hint')}</p>
    <ul>
      {#each proposals as p (p.intent.id)}
        {@const left = secondsLeft(p.expires_at, now)}
        {@const text = describeAction(p.intent.action, tPower)}
        {@const busy = control.busy[p.intent.id] ?? false}
        {@const error = control.errors[p.intent.id] ?? null}
        <li data-testid="proposal">
          <div class="head">
            <strong>{names.get(p.intent.asset_id) ?? p.intent.asset_id}</strong>
            <span>{t(text.key, text.args)}</span>
            {#if left !== null}
              <span class="chip-tag" class:warn={left <= 30} data-testid="countdown">
                {left === 0
                  ? t('proposals.expired')
                  : t('proposals.expires_in', { time: clock(left) })}
              </span>
            {/if}
          </div>
          <ReasonList reasons={p.intent.reasons} />
          {#if error}
            <p class="err" role="alert">{tDynamic(error, undefined, 'api.error.unknown')}</p>
          {/if}
          <div class="actions">
            <button
              type="button"
              class="btn small"
              disabled={busy || left === 0}
              data-testid="proposal-confirm"
              onclick={() => void control.confirmProposal(p.intent.id)}
              >{t('proposals.confirm')}</button
            >
            <button
              type="button"
              class="btn small secondary"
              disabled={busy}
              data-testid="proposal-dismiss"
              onclick={() => void control.dismissProposal(p.intent.id)}
              >{t('proposals.dismiss')}</button
            >
          </div>
        </li>
      {/each}
    </ul>
  </section>
{/if}

<style>
  .proposals {
    display: grid;
    gap: 8px;
  }
  ul {
    list-style: none;
    margin: 0;
    padding: 0;
    display: grid;
    gap: 12px;
  }
  li {
    display: grid;
    gap: 6px;
    padding-top: 12px;
    border-top: 1px solid var(--divider-color);
  }
  li:first-child {
    border-top: 0;
    padding-top: 0;
  }
  .head {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 4px 12px;
  }
  .actions {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }
  .err {
    margin: 0;
    color: var(--error-color);
  }
</style>
