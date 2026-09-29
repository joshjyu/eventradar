/**
 * Starts the daily pipeline from a Cloudflare Cron Trigger.
 *
 * GitHub disables `schedule` triggers in public repositories after 60 days
 * without commits. The pipeline never commits, so this Worker starts it
 * through the `workflow_dispatch` API instead, which has no such limit.
 */

export interface Env {
  /** Fine-grained token: this repository only, Actions read/write. */
  GITHUB_TOKEN: string;
  GITHUB_REPOSITORY: string;
  WORKFLOW_FILE: string;
  WORKFLOW_REF: string;
}

const GITHUB_API = "https://api.github.com";
// GitHub owners are alphanumeric with hyphens; repos may also use . and _.
const NAME = /^[A-Za-z0-9-]+\/(?!\.\.?$)[\w.-]+$/;

/**
 * Ask GitHub to run the configured workflow.
 *
 * @param env Worker bindings.
 * @param fetchImpl HTTP client; injectable for tests.
 * @returns Resolves once GitHub accepts the dispatch.
 */
export async function dispatchWorkflow(
  env: Env,
  fetchImpl: typeof fetch = fetch,
): Promise<void> {
  if (!env.GITHUB_TOKEN) {
    throw new Error("GITHUB_TOKEN secret is not set");
  }
  if (!NAME.test(env.GITHUB_REPOSITORY)) {
    throw new Error(`invalid GITHUB_REPOSITORY: ${env.GITHUB_REPOSITORY}`);
  }
  const workflow = encodeURIComponent(env.WORKFLOW_FILE);
  const url =
    `${GITHUB_API}/repos/${env.GITHUB_REPOSITORY}` +
    `/actions/workflows/${workflow}/dispatches`;
  const response = await fetchImpl(url, {
    method: "POST",
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      "Content-Type": "application/json",
      "User-Agent": "eventradar-scheduler",
      "X-GitHub-Api-Version": "2022-11-28",
    },
    body: JSON.stringify({ ref: env.WORKFLOW_REF }),
  });
  if (!response.ok) {
    // GitHub error bodies never echo the token.
    const detail = (await response.text()).slice(0, 300);
    throw new Error(`dispatch failed: HTTP ${response.status} ${detail}`);
  }
}

export default {
  /**
   * Cron entry point; a thrown error marks the invocation as failed.
   *
   * @param _controller Scheduled event details.
   * @param env Worker bindings.
   * @returns Resolves when the dispatch is accepted.
   */
  async scheduled(_controller, env): Promise<void> {
    await dispatchWorkflow(env);
  },
} satisfies ExportedHandler<Env>;
