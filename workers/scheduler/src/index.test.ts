import { describe, expect, it, vi } from "vitest";

import { dispatchWorkflow, type Env } from "./index";

const ENV: Env = {
  GITHUB_TOKEN: "test-token",
  GITHUB_REPOSITORY: "owner/repo",
  WORKFLOW_FILE: "daily.yml",
  WORKFLOW_REF: "main",
};

/**
 * Build a fetch stub that returns one response.
 *
 * @param status HTTP status to return.
 * @param body Response body.
 * @returns A mocked fetch.
 */
function stub(status: number, body = ""): typeof fetch {
  return vi.fn(async () => new Response(body || null, { status }));
}

describe("dispatchWorkflow", () => {
  it("posts a workflow_dispatch for the configured ref", async () => {
    const fetchImpl = stub(204);
    await dispatchWorkflow(ENV, fetchImpl);
    const [url, init] = vi.mocked(fetchImpl).mock.calls[0];
    expect(url).toBe(
      "https://api.github.com/repos/owner/repo/actions/workflows/daily.yml/dispatches",
    );
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({ ref: "main" });
    const headers = init?.headers as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer test-token");
  });

  it("fails loudly on a GitHub error", async () => {
    await expect(
      dispatchWorkflow(ENV, stub(403, '{"message":"Resource not accessible"}')),
    ).rejects.toThrow("HTTP 403");
  });

  it("refuses to run without a token or with a bad repository", async () => {
    await expect(
      dispatchWorkflow({ ...ENV, GITHUB_TOKEN: "" }, stub(204)),
    ).rejects.toThrow("GITHUB_TOKEN");
    for (const bad of ["../x", "owner/..", "owner/a/b", "owner"]) {
      await expect(
        dispatchWorkflow({ ...ENV, GITHUB_REPOSITORY: bad }, stub(204)),
      ).rejects.toThrow("invalid GITHUB_REPOSITORY");
    }
  });
});
