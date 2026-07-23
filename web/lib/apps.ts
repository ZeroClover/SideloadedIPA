/** Validated, explicitly cached access to the R2 application registry. */

import fixtureRegistry from "@/fixtures/apps.json";

const SLUG_PATTERN = /^[A-Za-z0-9._-]+$/;

export interface AppEntry {
  slug: string;
  name: string;
  bundleId: string;
  version: string;
  ipaUrl: string;
  /** Empty means that the publisher has not supplied an icon yet. */
  iconUrl: string;
}

export class AppsRegistryError extends Error {
  constructor(
    public readonly field: string,
    message: string,
  ) {
    super(`${message} [${field}]`);
    this.name = "AppsRegistryError";
  }
}

export interface RegistryRequestInit extends RequestInit {
  cache: "force-cache";
  next: { tags: ["apps"] };
}

export type RegistryFetch = (url: string, init: RegistryRequestInit) => Promise<Response>;

interface AppsLoaderDependencies {
  env?: Record<string, string | undefined>;
  fetcher?: RegistryFetch;
  fixture?: unknown;
}

function fail(field: string, message: string): never {
  throw new AppsRegistryError(field, message);
}

function objectValue(value: unknown, field: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    fail(field, "application registry value must be an object");
  }
  return value as Record<string, unknown>;
}

function stringValue(value: unknown, field: string): string {
  if (typeof value !== "string" || value.trim() === "") {
    fail(field, "application registry field must be a non-empty string");
  }
  return value.trim();
}

function httpsUrl(value: unknown, field: string, allowEmpty = false): string {
  if (allowEmpty && value === "") {
    return "";
  }
  const text = stringValue(value, field);
  let parsed: URL;
  try {
    parsed = new URL(text);
  } catch {
    fail(field, "application registry URL must be valid HTTPS");
  }
  if (
    parsed.protocol !== "https:" ||
    parsed.hostname === "" ||
    parsed.username !== "" ||
    parsed.password !== ""
  ) {
    fail(field, "application registry URL must be valid HTTPS");
  }
  return text;
}

function decodeEntry(value: unknown, index: number, slugs: Set<string>): AppEntry {
  const field = `apps[${index}]`;
  const entry = objectValue(value, field);
  const slug = stringValue(entry.slug, `${field}.slug`);
  if (!SLUG_PATTERN.test(slug) || slugs.has(slug)) {
    fail(`${field}.slug`, "application slug is invalid or duplicated");
  }
  slugs.add(slug);
  return Object.freeze({
    slug,
    name: stringValue(entry.name, `${field}.name`),
    bundleId: stringValue(entry.bundleId, `${field}.bundleId`),
    version: stringValue(entry.version, `${field}.version`),
    ipaUrl: httpsUrl(entry.ipaUrl, `${field}.ipaUrl`),
    iconUrl: httpsUrl(entry.iconUrl, `${field}.iconUrl`, true),
  });
}

export function decodeAppsRegistry(value: unknown): AppEntry[] {
  const root = objectValue(value, "root");
  if (!Array.isArray(root.apps)) {
    fail("apps", "application registry apps field must be an array");
  }
  const slugs = new Set<string>();
  return Object.freeze(root.apps.map((entry, index) => decodeEntry(entry, index, slugs))).slice();
}

function dataMode(env: Record<string, string | undefined>): "fixture" | "origin" {
  const mode = env.APPS_DATA_MODE;
  if (mode !== "fixture" && mode !== "origin") {
    fail("APPS_DATA_MODE", "APPS_DATA_MODE must explicitly select fixture or origin");
  }
  if (mode === "fixture" && env.VERCEL_ENV === "production") {
    fail("APPS_DATA_MODE", "fixture registry mode is forbidden in production deployments");
  }
  return mode;
}

async function readOrigin(url: string, fetcher: RegistryFetch): Promise<AppEntry[]> {
  let response: Response;
  try {
    response = await fetcher(url, {
      cache: "force-cache",
      next: { tags: ["apps"] },
    });
  } catch {
    fail("origin", "application registry origin request failed");
  }
  if (!response.ok) {
    fail("origin", `application registry origin returned HTTP ${response.status}`);
  }
  let document: unknown;
  try {
    document = await response.json();
  } catch {
    fail("origin", "application registry origin did not return valid JSON");
  }
  return decodeAppsRegistry(document);
}

export async function getApps(
  dependencies: AppsLoaderDependencies = {},
): Promise<AppEntry[]> {
  const env = dependencies.env ?? process.env;
  if (dataMode(env) === "fixture") {
    return decodeAppsRegistry(dependencies.fixture ?? fixtureRegistry);
  }

  const origin = httpsUrl(env.R2_APPS_JSON_URL, "R2_APPS_JSON_URL");
  const fetcher: RegistryFetch =
    dependencies.fetcher ?? ((url, init) => fetch(url, init));
  return readOrigin(origin, fetcher);
}
