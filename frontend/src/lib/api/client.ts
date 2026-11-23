// SPDX-License-Identifier: Apache-2.0
// REST client. Every URL is resolved against the current document so the Home Assistant
// ingress base path (dynamic, unknown at build time) never matters. Never use `/api/...`.
import type {
  CheckResult,
  ConfigHistoryEntry,
  ConfigSectionName,
  ControlState,
  Decision,
  DecisionKind,
  DeviceControlState,
  OperatingMode,
  OperationConfig,
  OverrideCommand,
  EnergyPrefs,
  EntityCandidate,
  ForecastAccuracyView,
  HistoryRestore,
  ImportResult,
  Info,
  PlanStatus,
  PlanView,
  PreviewResult,
  PriceReport,
  PvForecastView,
  ReplanAck,
  RiskView,
  SiteConfig,
  TemplateDetail,
  TemplateKind,
  TemplateList,
  ValidationIssue,
} from './types';

export const API_PREFIX = 'api/v1/';

/** Resolve a path relative to the current document (works below any ingress prefix). */
export function apiUrl(path: string, base: string = location.href): URL {
  return new URL(path.replace(/^\/+/, ''), base);
}

/** URL of the live WebSocket (ws/wss follows the page scheme). */
export function liveUrl(base: string = location.href): string {
  const url = apiUrl(`${API_PREFIX}live`, base);
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
  return url.toString();
}

export class ApiError extends Error {
  /** Catalogue key (`api.error.*`) when the server sent one, else `null`. */
  readonly key: string | null;
  readonly issues: ValidationIssue[];
  constructor(
    readonly status: number,
    key: string | null,
    issues: ValidationIssue[] = [],
  ) {
    super(key ?? `HTTP ${status}`);
    this.name = 'ApiError';
    this.key = key;
    this.issues = issues;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(apiUrl(path), {
      ...init,
      headers: { Accept: 'application/json', ...(init?.headers ?? {}) },
    });
  } catch {
    throw new ApiError(0, 'api.error.network');
  }
  if (!res.ok) {
    let key: string | null = null;
    let issues: ValidationIssue[] = [];
    try {
      const body = (await res.json()) as { detail?: unknown; issues?: unknown };
      if (typeof body.detail === 'string') key = body.detail;
      if (Array.isArray(body.issues)) issues = body.issues as ValidationIssue[];
    } catch {
      // Non-JSON error body (e.g. a proxy page): fall through to the generic key.
    }
    throw new ApiError(res.status, key, issues);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

const JSON_HEADERS = { 'Content-Type': 'application/json' };

function send<T>(method: 'POST' | 'PUT', path: string, body?: unknown): Promise<T> {
  return request<T>(
    path,
    body === undefined ? { method } : { method, headers: JSON_HEADERS, body: JSON.stringify(body) },
  );
}

export const api = {
  info: () => request<Info>(`${API_PREFIX}info`),
  getSite: () => request<SiteConfig | null>(`${API_PREFIX}config/site`),
  putSite: (site: SiteConfig) =>
    request<SiteConfig>(`${API_PREFIX}config/site`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(site),
    }),
  entities: () => request<EntityCandidate[]>(`${API_PREFIX}entities`),
  /** Ranked entities for a site role (`grid_power`, `battery_soc`, ...). */
  entitiesForRole: (role: string, includeHidden = false) =>
    request<EntityCandidate[]>(
      `${API_PREFIX}entities${query({ role, include_hidden: includeHidden ? 'true' : undefined })}`,
    ),
  /**
   * Ranked entities for a template role; `deviceId` scopes `same_device_as` roles, `deviceName`
   * (name of the device already mapped) only ranks same-named candidates first.
   */
  entitiesForTemplate: (
    template: string,
    role: string,
    deviceId?: string,
    includeHidden = false,
    deviceName?: string,
  ) =>
    request<EntityCandidate[]>(
      `${API_PREFIX}entities${query({
        template,
        role,
        device_id: deviceId,
        device_name: deviceName,
        include_hidden: includeHidden ? 'true' : undefined,
      })}`,
    ),
  /** A config section; `null` while it was never stored. */
  getSection: <T>(section: ConfigSectionName) =>
    request<T | null>(`${API_PREFIX}config/${section}`),
  putSection: <T>(section: ConfigSectionName, body: T) =>
    request<T>(`${API_PREFIX}config/${section}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  history: (section: ConfigSectionName) =>
    request<ConfigHistoryEntry[]>(`${API_PREFIX}config/${section}/history`),
  restore: (section: ConfigSectionName, id: number) =>
    request<HistoryRestore>(`${API_PREFIX}config/${section}/history/${id}/restore`, {
      method: 'POST',
    }),
  /** URL of the configuration export (a plain download link, resolved against the document). */
  exportUrl: (): string => apiUrl(`${API_PREFIX}config/export`).toString(),
  /** Import an export file (raw JSON text, validated by the server). */
  importConfig: (text: string) =>
    request<ImportResult>(`${API_PREFIX}config/import`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: text,
    }),
  templates: (kind?: TemplateKind) =>
    request<TemplateList>(`${API_PREFIX}templates${query({ kind })}`),
  template: (id: string) =>
    request<TemplateDetail>(`${API_PREFIX}templates/${encodeURIComponent(id)}`),
  energyPrefs: () => request<EnergyPrefs>(`${API_PREFIX}wizard/energy-prefs`),
  /** System check of the stored site, or of an unsaved one. */
  check: (site?: SiteConfig) =>
    request<CheckResult>(`${API_PREFIX}wizard/check`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(site ? { site } : {}),
    }),
  /** Live values of every entity in an unsaved SiteConfig or DeviceConfig fragment. */
  preview: (fragment: Record<string, unknown>) =>
    request<PreviewResult>(`${API_PREFIX}preview`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(fragment),
    }),
};

export const controlApi = {
  state: () => request<ControlState>(`${API_PREFIX}control/state`),
  emergencyStop: () => send<{ active: boolean }>('POST', `${API_PREFIX}control/emergency-stop`),
  clearEmergencyStop: () =>
    send<{ active: boolean }>('POST', `${API_PREFIX}control/emergency-stop/clear`),
  override: (deviceId: string, command: OverrideCommand) =>
    send<DeviceControlState>(
      'POST',
      `${API_PREFIX}devices/${encodeURIComponent(deviceId)}/override`,
      command,
    ),
  proposals: () => request<Decision[]>(`${API_PREFIX}proposals`),
  confirmProposal: (intentId: string) =>
    send<Decision>('POST', `${API_PREFIX}proposals/${encodeURIComponent(intentId)}/confirm`),
  dismissProposal: (intentId: string) =>
    send<void>('POST', `${API_PREFIX}proposals/${encodeURIComponent(intentId)}/dismiss`),
  /** The decision log, newest first; `kinds` and `deviceId` filter on the server. */
  decisions: (
    opts: { limit?: number; deviceId?: string; kinds?: readonly DecisionKind[] } = {},
  ) => {
    const search = new URLSearchParams();
    search.set('limit', String(opts.limit ?? 100));
    if (opts.deviceId) search.set('device_id', opts.deviceId);
    for (const k of opts.kinds ?? []) search.append('kind', k);
    return request<Decision[]>(`${API_PREFIX}decisions?${search.toString()}`);
  },
  setGlobalMode: (mode: OperatingMode) =>
    request<OperationConfig>(`${API_PREFIX}operation/mode`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode }),
    }),
  /** The battery's mode; above shadow it needs the identity token of the control state. */
  setBatteryMode: (mode: OperatingMode, identity: string | null) =>
    request<OperationConfig>(`${API_PREFIX}operation/battery`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode, identity }),
    }),
  setDeviceMode: (id: string, mode: OperatingMode, identity: string | null) =>
    request<OperationConfig>(`${API_PREFIX}operation/devices/${encodeURIComponent(id)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode, identity }),
    }),
};

/** The planner and its inputs (WP9): plan, status, prices and forecasts (all read only). */
export const planApi = {
  plan: (hours = 48) => request<PlanView>(`${API_PREFIX}plan${query({ hours: String(hours) })}`),
  status: () => request<PlanStatus>(`${API_PREFIX}plan/status`),
  /** Ask for a new plan; the server debounces (`accepted: false` names the earliest start). */
  replan: () => send<ReplanAck>('POST', `${API_PREFIX}plan/replan`),
  prices: (hours = 48) =>
    request<PriceReport>(`${API_PREFIX}prices${query({ hours: String(hours) })}`),
  forecastPv: (hours = 48) =>
    request<PvForecastView>(`${API_PREFIX}forecast/pv${query({ hours: String(hours) })}`),
  forecastAccuracy: () => request<ForecastAccuracyView>(`${API_PREFIX}forecast/accuracy`),
  forecastRisk: () => request<RiskView>(`${API_PREFIX}forecast/risk`),
};

function query(params: Record<string, string | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) search.set(key, value);
  }
  const text = search.toString();
  return text ? `?${text}` : '';
}
