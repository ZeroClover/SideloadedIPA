import assert from "node:assert/strict";
import { describe, it } from "node:test";

import type { AppEntry } from "../lib/apps";
import { handleItmsRequest } from "../lib/itms-route";
import { handleRevalidation } from "../lib/revalidation";

const app: AppEntry = {
  slug: "special",
  name: "A&B <Special>",
  bundleId: "io.example.a&b",
  version: "1<2",
  ipaUrl: "https://downloads.example/A&B.ipa?x=1&y=2",
  iconUrl: "",
};

describe("registry revalidation request", () => {
  it("rejects a query-string or incorrect secret without changing cache state", async () => {
    const calls: Array<[string, string]> = [];
    const revalidate = (tag: string, profile: string) => calls.push([tag, profile]);

    const queryOnly = await handleRevalidation(
      new Request("https://site.example/api/revalidate?secret=reviewed-secret"),
      "reviewed-secret",
      revalidate,
    );
    const incorrect = await handleRevalidation(
      new Request("https://site.example/api/revalidate", {
        headers: { "x-revalidate-secret": "wrong" },
      }),
      "reviewed-secret",
      revalidate,
    );

    assert.equal(queryOnly.status, 401);
    assert.equal(incorrect.status, 401);
    assert.deepEqual(calls, []);
  });

  it("marks only the apps tag stale with the max profile", async () => {
    const calls: Array<[string, string]> = [];
    const response = await handleRevalidation(
      new Request("https://site.example/api/revalidate", {
        headers: { "x-revalidate-secret": "reviewed-secret" },
      }),
      "reviewed-secret",
      (tag, profile) => calls.push([tag, profile]),
    );

    assert.equal(response.status, 200);
    assert.deepEqual(calls, [["apps", "max"]]);
    assert.doesNotMatch(await response.text(), /reviewed-secret/);
  });
});

describe("ITMS request", () => {
  it("renders one validated entry with escaped XML and revalidation headers", async () => {
    const response = await handleItmsRequest("special", async () => [app]);
    const body = await response.text();

    assert.equal(response.status, 200);
    assert.equal(response.headers.get("content-type"), "text/xml; charset=utf-8");
    assert.equal(
      response.headers.get("cache-control"),
      "public, max-age=0, must-revalidate",
    );
    assert.match(body, /https:\/\/downloads\.example\/A&amp;B\.ipa\?x=1&amp;y=2/);
    assert.match(body, /io\.example\.a&amp;b/);
    assert.match(body, /1&lt;2/);
    assert.match(body, /A&amp;B &lt;Special&gt;/);
  });

  it("returns not found for an unknown slug without constructing a URL", async () => {
    const response = await handleItmsRequest("missing", async () => [app]);

    assert.equal(response.status, 404);
    assert.equal(await response.text(), "not found");
  });

  it("propagates registry failures instead of synthesizing an empty catalog", async () => {
    await assert.rejects(() =>
      handleItmsRequest("special", async () => {
        throw new Error("registry unavailable");
      }),
    );
  });
});
