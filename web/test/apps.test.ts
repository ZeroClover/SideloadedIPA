import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  AppsRegistryError,
  decodeAppsRegistry,
  getApps,
  type RegistryFetch,
} from "../lib/apps";

const validRegistry = {
  updatedAt: "2026-07-23T00:00:00Z",
  apps: [
    {
      slug: "example",
      name: "Example & App",
      bundleId: "io.example.app",
      version: "1.2.3",
      ipaUrl: "https://downloads.example/apps/example/App.ipa",
      iconUrl: "https://downloads.example/apps/example/icon.png",
    },
  ],
};

describe("apps registry decoder", () => {
  it("returns typed values from a valid registry", () => {
    assert.deepEqual(decodeAppsRegistry(validRegistry), validRegistry.apps);
  });

  const invalidCases: Array<[string, unknown, string]> = [
    ["null root", null, "root"],
    ["array root", [], "root"],
    ["missing apps", {}, "apps"],
    ["non-array apps", { apps: {} }, "apps"],
    ["non-object entry", { apps: ["secret-entry"] }, "apps[0]"],
    [
      "missing name",
      { apps: [{ ...validRegistry.apps[0], name: "" }] },
      "apps[0].name",
    ],
    [
      "invalid slug",
      { apps: [{ ...validRegistry.apps[0], slug: "bad/slug" }] },
      "apps[0].slug",
    ],
    [
      "duplicate slug",
      { apps: [validRegistry.apps[0], { ...validRegistry.apps[0] }] },
      "apps[1].slug",
    ],
    [
      "insecure IPA URL",
      { apps: [{ ...validRegistry.apps[0], ipaUrl: "http://secret.example/App.ipa" }] },
      "apps[0].ipaUrl",
    ],
    [
      "insecure icon URL",
      { apps: [{ ...validRegistry.apps[0], iconUrl: "http://secret.example/icon.png" }] },
      "apps[0].iconUrl",
    ],
  ];

  for (const [name, registry, field] of invalidCases) {
    it(`rejects ${name} with a redacted field diagnostic`, () => {
      assert.throws(
        () => decodeAppsRegistry(registry),
        (error: unknown) => {
          assert.ok(error instanceof AppsRegistryError);
          assert.equal(error.field, field);
          assert.doesNotMatch(error.message, /secret-entry|secret\.example/);
          return true;
        },
      );
    });
  }
});

describe("apps registry data mode and cache contract", () => {
  it("requires explicit data mode", async () => {
    await assert.rejects(() => getApps({ env: {} }), /APPS_DATA_MODE/);
  });

  it("rejects missing production origin configuration", async () => {
    await assert.rejects(
      () => getApps({ env: { APPS_DATA_MODE: "origin", VERCEL_ENV: "production" } }),
      /R2_APPS_JSON_URL/,
    );
  });

  it("uses the validated fixture only in explicit non-production fixture mode", async () => {
    const apps = await getApps({
      env: { APPS_DATA_MODE: "fixture", VERCEL_ENV: "preview" },
      fixture: validRegistry,
    });

    assert.deepEqual(apps, validRegistry.apps);
  });

  it("rejects fixture mode in a production deployment", async () => {
    await assert.rejects(
      () =>
        getApps({
          env: { APPS_DATA_MODE: "fixture", VERCEL_ENV: "production" },
          fixture: validRegistry,
        }),
      /fixture/,
    );
  });

  it("uses an explicit persistent cache entry tagged apps", async () => {
    const calls: Array<{ url: string; init: Parameters<RegistryFetch>[1] }> = [];
    const fetcher: RegistryFetch = async (url, init) => {
      calls.push({ url, init });
      return Response.json(validRegistry);
    };

    const apps = await getApps({
      env: {
        APPS_DATA_MODE: "origin",
        R2_APPS_JSON_URL: "https://downloads.example/site/apps.json",
      },
      fetcher,
    });

    assert.deepEqual(apps, validRegistry.apps);
    assert.deepEqual(calls, [
      {
        url: "https://downloads.example/site/apps.json",
        init: { cache: "force-cache", next: { tags: ["apps"] } },
      },
    ]);
  });

  it("fails closed when the origin has no valid cached response", async () => {
    const fetcher: RegistryFetch = async () => new Response("unavailable", { status: 503 });

    await assert.rejects(
      () =>
        getApps({
          env: {
            APPS_DATA_MODE: "origin",
            R2_APPS_JSON_URL: "https://downloads.example/site/apps.json",
          },
          fetcher,
        }),
      (error: unknown) => {
        assert.ok(error instanceof AppsRegistryError);
        assert.equal(error.field, "origin");
        assert.doesNotMatch(error.message, /downloads\.example|unavailable/);
        return true;
      },
    );
  });
});
