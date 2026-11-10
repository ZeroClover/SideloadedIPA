/**
 * End-to-end delivery: a production build of the site, served by `next start`,
 * reading the registry from an HTTPS origin exactly as it reads R2.
 *
 * The only stand-in is the origin itself — a local HTTPS server holding
 * site/apps.json and the IPA, trusted by the site through NODE_EXTRA_CA_CERTS.
 */

import assert from "node:assert/strict";
import { type ChildProcess, execFileSync, spawn } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import https from "node:https";
import { type AddressInfo, createServer } from "node:net";
import { tmpdir } from "node:os";
import path from "node:path";
import { after, before, describe, it } from "node:test";

const WEB_ROOT = path.resolve(import.meta.dirname, "..");
const NEXT = path.join(WEB_ROOT, "node_modules", ".bin", "next");
const SECRET = "e2e-revalidate-secret";
const SITE_BASE_URL = "https://site.example";
const IPA_BYTES = Buffer.from("signed ipa bytes served by the origin");

let scratch: string;
let certificate: string;
let origin: https.Server;
let originUrl: string;
let site: ChildProcess;
let siteUrl: string;
let siteLog = "";
let published = { version: "1.0", ipaPath: "/apps/Fixture/1.0/Fixture.ipa" };

function registry(): string {
  return JSON.stringify({
    apps: [
      {
        slug: "Fixture",
        name: "Fixture App",
        bundleId: "io.example.e2e.fixture",
        version: published.version,
        ipaUrl: `${originUrl}${published.ipaPath}`,
        iconUrl: "",
      },
    ],
  });
}

async function freePort(): Promise<number> {
  const server = createServer();
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address() as AddressInfo;
  await new Promise((resolve) => server.close(resolve));
  return port;
}

function siteEnvironment(): NodeJS.ProcessEnv {
  return {
    PATH: process.env.PATH,
    HOME: process.env.HOME,
    NODE_ENV: "production",
    NEXT_TELEMETRY_DISABLED: "1",
    APPS_DATA_MODE: "origin",
    R2_APPS_JSON_URL: `${originUrl}/site/apps.json`,
    REVALIDATE_SECRET: SECRET,
    SITE_PUBLIC_BASE_URL: SITE_BASE_URL,
    NODE_EXTRA_CA_CERTS: path.join(scratch, "certificate.pem"),
  };
}

/** Build without blocking the event loop: prerendering reads the origin above. */
function build(): Promise<void> {
  return new Promise((resolve, reject) => {
    let log = "";
    const child = spawn(NEXT, ["build"], {
      cwd: WEB_ROOT,
      env: siteEnvironment(),
      stdio: ["ignore", "pipe", "pipe"],
    });
    child.stdout.on("data", (chunk) => (log += chunk));
    child.stderr.on("data", (chunk) => (log += chunk));
    child.on("error", reject);
    child.on("exit", (code) =>
      code === 0 ? resolve() : reject(new Error(`next build failed:\n${log}`)),
    );
  });
}

async function waitUntilServing(): Promise<void> {
  const deadline = Date.now() + 60_000;
  while (Date.now() < deadline) {
    if (site.exitCode !== null) break;
    try {
      await fetch(`${siteUrl}/apps/Fixture/itms.plist`);
      return;
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
  }
  throw new Error(`next start did not begin serving:\n${siteLog}`);
}

function downloadFromOrigin(url: string): Promise<Buffer> {
  return new Promise((resolve, reject) => {
    https
      .get(url, { ca: certificate }, (response) => {
        const chunks: Buffer[] = [];
        response.on("data", (chunk: Buffer) => chunks.push(chunk));
        response.on("end", () => resolve(Buffer.concat(chunks)));
      })
      .on("error", reject);
  });
}

async function manifest(): Promise<Response> {
  return fetch(`${siteUrl}/apps/Fixture/itms.plist`);
}

function revalidate(secret: string): Promise<Response> {
  return fetch(`${siteUrl}/api/revalidate`, {
    method: "POST",
    headers: { "x-revalidate-secret": secret },
  });
}

before(async () => {
  scratch = mkdtempSync(path.join(tmpdir(), "itms-e2e-"));
  execFileSync(
    "openssl",
    [
      "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
      "-keyout", path.join(scratch, "key.pem"),
      "-out", path.join(scratch, "certificate.pem"),
      "-subj", "/CN=localhost",
      "-addext", "subjectAltName=DNS:localhost",
    ],
    { stdio: "pipe" },
  );
  certificate = readFileSync(path.join(scratch, "certificate.pem"), "utf8");
  origin = https.createServer(
    { key: readFileSync(path.join(scratch, "key.pem")), cert: certificate },
    (request, response) => {
      if (request.url === "/site/apps.json") {
        response.writeHead(200, { "Content-Type": "application/json" }).end(registry());
      } else if (request.url === published.ipaPath) {
        response.writeHead(200, { "Content-Type": "application/octet-stream" }).end(IPA_BYTES);
      } else {
        response.writeHead(404).end();
      }
    },
  );
  await new Promise<void>((resolve) => origin.listen(0, "127.0.0.1", resolve));
  originUrl = `https://localhost:${(origin.address() as AddressInfo).port}`;

  await build();
  const port = await freePort();
  siteUrl = `http://127.0.0.1:${port}`;
  site = spawn(NEXT, ["start", "--hostname", "127.0.0.1", "--port", String(port)], {
    cwd: WEB_ROOT,
    env: siteEnvironment(),
    stdio: ["ignore", "pipe", "pipe"],
  });
  site.stdout?.on("data", (chunk) => (siteLog += chunk));
  site.stderr?.on("data", (chunk) => (siteLog += chunk));
  await waitUntilServing();
});

after(async () => {
  site?.kill();
  await new Promise((resolve) => (origin ? origin.close(resolve) : resolve(undefined)));
  origin?.closeAllConnections();
  if (scratch) rmSync(scratch, { recursive: true, force: true });
});

describe("published registry delivery", () => {
  it("lists the published app with its manifest on the public site origin", async () => {
    const response = await fetch(siteUrl);
    const page = await response.text();

    assert.equal(response.status, 200);
    assert.match(page, /Fixture App/);
    assert.ok(
      page.includes(`${SITE_BASE_URL}/apps/Fixture/itms.plist`),
      "the install button receives the manifest URL on the public site origin",
    );
  });

  it("serves a manifest whose package URL downloads the published IPA", async () => {
    const response = await manifest();
    const document = await response.text();
    const packageUrl = /<key>url<\/key>\s*<string>([^<]+)<\/string>/.exec(document)?.[1];

    assert.equal(response.status, 200);
    assert.equal(response.headers.get("content-type"), "text/xml; charset=utf-8");
    assert.equal(response.headers.get("cache-control"), "public, max-age=0, must-revalidate");
    assert.match(document, /<string>io\.example\.e2e\.fixture<\/string>/);
    assert.match(document, /<string>1\.0<\/string>/);
    assert.equal(packageUrl, `${originUrl}/apps/Fixture/1.0/Fixture.ipa`);
    assert.deepEqual(await downloadFromOrigin(packageUrl), IPA_BYTES);
  });

  it("answers an unknown app with not found", async () => {
    const response = await fetch(`${siteUrl}/apps/Missing/itms.plist`);

    assert.equal(response.status, 404);
  });

  it("keeps the cached registry until the publisher revalidates with the secret", async () => {
    published = { version: "2.0", ipaPath: "/apps/Fixture/2.0/Fixture.ipa" };

    const refused = await revalidate("wrong-secret");
    const stale = await (await manifest()).text();
    const accepted = await revalidate(SECRET);
    const fresh = await (await manifest()).text();

    assert.equal(refused.status, 401);
    assert.match(stale, /<string>1\.0<\/string>/);
    assert.equal(accepted.status, 200);
    assert.match(fresh, /<string>2\.0<\/string>/);
    assert.ok(fresh.includes(`${originUrl}/apps/Fixture/2.0/Fixture.ipa`));
  });
});
