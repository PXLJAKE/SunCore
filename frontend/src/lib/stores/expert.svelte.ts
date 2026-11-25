// SPDX-License-Identifier: Apache-2.0
// Expert mode switch: per browser (localStorage), off by default. It only reveals UI; the
// server applies the same limits and confirmations either way.
const KEY = 'suncore.expert';

function read(): boolean {
  try {
    return localStorage.getItem(KEY) === '1';
  } catch {
    return false;
  }
}

class ExpertMode {
  enabled = $state(read());

  set(on: boolean): void {
    this.enabled = on;
    try {
      if (on) localStorage.setItem(KEY, '1');
      else localStorage.removeItem(KEY);
    } catch {
      // Storage blocked (private mode): the switch then lasts for this page load only.
    }
  }
}

export const expert = new ExpertMode();
