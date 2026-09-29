/**
 * Serves published feed files from R2 at /v1/..., same-origin with the
 * page. The bucket is bound as FEED_BUCKET in the Pages project settings,
 * so no bucket name or URL lives in the repository.
 */

// Only published outputs; anything else in the bucket stays private.
const ALLOWED = new RegExp(
  "^v1/(schema/event\\.json|[a-z0-9-]+/(events\\.json|events\\.ics|" +
    "feed\\.xml|manifest\\.json|new/\\d{4}-\\d{2}-\\d{2}\\.json))$",
);

/**
 * Handle GET and HEAD for feed files.
 *
 * @param {{request: Request, env: {FEED_BUCKET: R2Bucket}, params: {path?: string[]}}} context
 *   Pages Function context.
 * @returns {Promise<Response>} The file, 304, 404, or 405.
 */
export async function onRequest({ request, env, params }) {
  if (request.method !== "GET" && request.method !== "HEAD") {
    return new Response("Method Not Allowed", {
      status: 405,
      headers: { allow: "GET, HEAD" },
    });
  }
  const key = ["v1", ...(params.path || [])].join("/");
  if (!ALLOWED.test(key)) {
    return new Response("Not Found", { status: 404 });
  }
  const object = await env.FEED_BUCKET.get(key, { onlyIf: request.headers });
  if (object === null) {
    return new Response("Not Found", { status: 404 });
  }
  const headers = new Headers();
  object.writeHttpMetadata(headers);
  headers.set("etag", object.httpEtag);
  headers.set("x-content-type-options", "nosniff");
  // The feed is a public API; other sites may read it.
  headers.set("access-control-allow-origin", "*");
  if (!headers.has("cache-control")) {
    headers.set("cache-control", "public, max-age=300");
  }
  // With a matching If-None-Match, R2 returns metadata without a body.
  if (!("body" in object)) {
    return new Response(null, { status: 304, headers });
  }
  const body = request.method === "HEAD" ? null : object.body;
  return new Response(body, { headers });
}
