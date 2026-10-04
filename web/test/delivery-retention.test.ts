import assert from "node:assert/strict";
import { it } from "node:test";
import { decodeAppsRegistry, getApps } from "../lib/apps";
import { handleRevalidation } from "../lib/revalidation";

const app = {
  slug: "example", name: "Example", bundleId: "io.example.app", version: "1.0",
  ipaUrl: "https://cdn.example/App.ipa", iconUrl: "",
};

for (const value of ["bad\u0000text", "bad\u000btext", "bad\ufffetext", "bad\ud800text"]) {
  it("rejects XML-invalid registry text", () => {
    assert.throws(() => decodeAppsRegistry({ apps: [{ ...app, name: value }] }));
  });
}
for (const slug of [".", ".."]) {
  it("rejects dot-path slugs", () => assert.throws(() => decodeAppsRegistry({ apps: [{ ...app, slug }] })));
}
for (const ipaUrl of ["https://cdn.example/App.ipa#fragment", "https://cdn.example/A\np.ipa", "https://user:pass@cdn.example/App.ipa"]) {
  it("rejects ambiguous artifact URLs", () => assert.throws(() => decodeAppsRegistry({ apps: [{ ...app, ipaUrl }] })));
}
it("accepts valid non-BMP XML and projects public fields only", () => {
  assert.deepEqual(decodeAppsRegistry({ apps: [{ ...app, name: "App 🚀", privateField: "secret" }] }), [{ ...app, name: "App 🚀" }]);
});
it("sets a ten-second timeout for origin fetches", async (t) => {
  const timeout = t.mock.method(AbortSignal, "timeout", (milliseconds: number) => {
    assert.equal(milliseconds, 10_000);
    return new AbortController().signal;
  });
  await getApps({
    env: { APPS_DATA_MODE: "origin", R2_APPS_JSON_URL: "https://cdn.example/apps.json" },
    fetcher: async () => Response.json({ apps: [app] }),
  });
  assert.equal(timeout.mock.callCount(), 1);
});
it("does not allow authenticated GET to mutate cache", async () => {
  const response = await handleRevalidation(new Request("https://site.example/api/revalidate", {
    headers: { "x-revalidate-secret": "secret" },
  }), "secret", () => assert.fail("must not expire"));
  assert.equal(response.status, 405);
  assert.equal(response.headers.get("allow"), "POST");
});
it("rejects missing configured secret", async () => {
  const response = await handleRevalidation(new Request("https://site.example/api/revalidate", {
    method: "POST", headers: { "x-revalidate-secret": "secret" },
  }), undefined, () => assert.fail("must not expire"));
  assert.equal(response.status, 401);
});
for (const fetcher of [async () => { throw new Error("private transport"); }, async () => new Response("not json")]) {
  it("redacts origin transport and JSON failures", async () => {
    await assert.rejects(() => getApps({ env: { APPS_DATA_MODE: "origin", R2_APPS_JSON_URL: "https://cdn.example/apps.json" }, fetcher }), (error: Error) => {
      assert.doesNotMatch(error.message, /private|not json|cdn.example/);
      return true;
    });
  });
}
