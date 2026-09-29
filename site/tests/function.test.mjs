// Tests for the feed-serving Pages Function, with a fake R2 bucket.
import assert from "node:assert/strict";
import { test } from "node:test";

import { onRequest } from "../functions/v1/[[path]].js";

/**
 * Build a fake R2 bucket holding the given objects.
 *
 * @param {Record<string, {body: string, type: string, etag: string}>} objects
 *   Keys to objects.
 * @returns {{get: Function, calls: string[]}} Bucket stand-in.
 */
function bucket(objects) {
  const calls = [];
  return {
    calls,
    async get(key, options) {
      calls.push(key);
      const object = objects[key];
      if (!object) return null;
      const meta = {
        httpEtag: `"${object.etag}"`,
        writeHttpMetadata(headers) {
          headers.set("content-type", object.type);
        },
      };
      const match = options?.onlyIf?.get?.("if-none-match");
      if (match === `"${object.etag}"`) return meta;
      return { ...meta, body: object.body };
    },
  };
}

const FEED = {
  "v1/socal-tech/events.json": {
    body: '{"events": []}',
    type: "application/json; charset=utf-8",
    etag: "abc",
  },
  "state/eventradar.db": { body: "secret", type: "x", etag: "zzz" },
};

/**
 * Call the function for a path.
 *
 * @param {string} path Request path under /v1/.
 * @param {object} [init] Request options.
 * @param {object} [store] Bucket objects.
 * @returns {Promise<{response: Response, env: object}>} Result.
 */
async function call(path, init = {}, store = FEED) {
  const env = { FEED_BUCKET: bucket(store) };
  const request = new Request(`https://example.test/v1/${path}`, init);
  const params = { path: path.split("/") };
  return { response: await onRequest({ request, env, params }), env };
}

test("serves a published file with metadata and CORS", async () => {
  const { response } = await call("socal-tech/events.json");
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("content-type"), "application/json; charset=utf-8");
  assert.equal(response.headers.get("etag"), '"abc"');
  assert.equal(response.headers.get("access-control-allow-origin"), "*");
  assert.equal(await response.text(), '{"events": []}');
});

test("returns 304 when the ETag matches", async () => {
  const { response } = await call("socal-tech/events.json", {
    headers: { "if-none-match": '"abc"' },
  });
  assert.equal(response.status, 304);
  assert.equal(await response.text(), "");
});

test("never exposes keys outside the published set", async () => {
  for (const path of ["../state/eventradar.db", "socal-tech/secret.json", "x"]) {
    const { response, env } = await call(path);
    assert.equal(response.status, 404, path);
    assert.deepEqual(env.FEED_BUCKET.calls, [], path);
  }
});

test("missing files are 404 and writes are refused", async () => {
  assert.equal((await call("socal-tech/feed.xml")).response.status, 404);
  const { response } = await call("socal-tech/events.json", { method: "POST" });
  assert.equal(response.status, 405);
  assert.equal(response.headers.get("allow"), "GET, HEAD");
});

test("HEAD returns headers without a body", async () => {
  const { response } = await call("socal-tech/events.json", { method: "HEAD" });
  assert.equal(response.status, 200);
  assert.equal(await response.text(), "");
});
