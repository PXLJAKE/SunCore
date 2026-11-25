// SPDX-License-Identifier: Apache-2.0
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, controlApi, flexApi, request, verificationApi } from './client';

function captureFetch(): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn(
    async () =>
      new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } }),
  );
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function sentHeaders(fetchMock: ReturnType<typeof vi.fn>, call: number): Record<string, string> {
  const init = fetchMock.mock.calls[call]?.[1] as RequestInit;
  return init.headers as Record<string, string>;
}

afterEach(() => vi.unstubAllGlobals());

describe('request header (SEC-13)', () => {
  it('goes with every request: reads, bodiless writes and JSON writes', async () => {
    const fetchMock = captureFetch();
    await api.info();
    await controlApi.emergencyStop();
    await controlApi.clearEmergencyStop();
    await flexApi.cancelProgram('dw');
    await controlApi.setGlobalMode('shadow');
    expect(fetchMock).toHaveBeenCalledTimes(5);
    for (let i = 0; i < 5; i++) {
      expect(sentHeaders(fetchMock, i)['X-SunCore-Request']).toBe('1');
    }
    expect(sentHeaders(fetchMock, 4)['Content-Type']).toBe('application/json');
  });

  it('goes with the verification commands (7-J), which do not use the shared helper', async () => {
    const fetchMock = captureFetch();
    await verificationApi.start();
    await verificationApi.confirm();
    await verificationApi.abort();
    expect(fetchMock).toHaveBeenCalledTimes(3);
    for (let i = 0; i < 3; i++) {
      expect(sentHeaders(fetchMock, i)['X-SunCore-Request']).toBe('1');
    }
  });

  it('cannot be overridden by a caller', async () => {
    const fetchMock = captureFetch();
    await request('api/v1/plan/replan', {
      method: 'POST',
      headers: { 'X-SunCore-Request': '0' },
    });
    expect(sentHeaders(fetchMock, 0)['X-SunCore-Request']).toBe('1');
  });
});
