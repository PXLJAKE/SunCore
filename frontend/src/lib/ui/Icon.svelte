<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import { ICONS, type IconName } from './icons';

  interface Props {
    name: IconName;
    /** Edge length in px. */
    size?: number;
  }
  let { name, size = 20 }: Props = $props();
</script>

<!-- Decorative: the text next to the icon (or an aria-label on the control) names it. -->
<svg
  class="icon"
  width={size}
  height={size}
  viewBox="0 0 24 24"
  aria-hidden="true"
  focusable="false"
  data-icon={name}
>
  <path d={ICONS[name]} />
</svg>

<style>
  .icon {
    flex: none;
    fill: none;
    stroke: currentColor;
    stroke-width: 2;
    stroke-linecap: round;
    stroke-linejoin: round;
  }
</style>
