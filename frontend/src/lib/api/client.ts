// SPDX-License-Identifier: Apache-2.0
// REST client. Every URL is resolved against the current document so the Home Assistant
// ingress base path (dynamic, unknown at build time) never matters. Never use `/api/...`.
import type {
  CheckResult,
  ConfigHistoryEntry,
  ConfigSectionName,
  EnergyPrefs,
  EntityCandidate,
  HistoryRestore,
  Info,
  PreviewResult,
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
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === 'string') key = body.detail;
      else if (Array.isArray(body.detail)) issues = body.detail as ValidationIssue[];
    } catch {
      // Non-JSON error body (e.g. a proxy page): fall through to the generic key.
    }
    throw new ApiError(res.status, key, issues);
  }
  return (await res.json()) as T;
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
  entitiesForRole: (role: string) =>
    request<EntityCandidate[]>(`${API_PREFIX}entities${query({ role })}`),
  /** Ranked entities for a template role; `deviceId` scopes `same_device_as` roles. */
  entitiesForTemplate: (template: string, role: string, deviceId?: string) =>
    request<EntityCandidate[]>(
      `${API_PREFIX}entities${query({ template, role, device_id: deviceId })}`,
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

function query(params: Record<string, string | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) search.set(key, value);
  }
  const text = search.toString();
  return text ? `?${text}` : '';
}
