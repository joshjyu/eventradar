# Scheduler (Cloudflare Worker)

`workers/scheduler` is a Cron Trigger Worker that starts the `daily`
workflow through GitHub's `workflow_dispatch` API every day at 12:00 UTC
(5:00am PDT, 4:00am PST).
GitHub disables `schedule` triggers in public repositories after 60 days
without commits; dispatches are not affected. The Worker has no HTTP
endpoint (`workers_dev` and preview URLs are off).

## One-time setup

1. **GitHub token.** Settings -> Developer settings -> Personal access
   tokens -> Fine-grained tokens -> Generate new token:
   - Name: `eventradar-scheduler`; expiration: 1 year
   - Repository access: **Only select repositories** -> `eventradar`
   - Repository permissions: **Actions: Read and write** (Metadata:
     read-only is added automatically). Nothing else.
2. **Deploy** (from `workers/scheduler`):

   ```bash
   npm ci
   npx wrangler login
   npx wrangler deploy
   npx wrangler secret put GITHUB_TOKEN
   ```

   `wrangler login` opens a browser; sign in to the Cloudflare account
   that should own the Worker. `secret put` prompts for the token without
   echoing it; paste it there and nowhere else.

## Verify

The next morning, the repository's Actions tab shows a `daily` run with
event `workflow_dispatch`. Failed invocations appear in the Cloudflare
dashboard under Workers -> `eventradar-scheduler` -> Logs.

## Changing the schedule

Edit `triggers.crons` in `workers/scheduler/wrangler.jsonc` (UTC), merge,
then redeploy from `workers/scheduler` on `main`:

```bash
npx wrangler deploy
```

The Worker is deployed by hand, not from Git, so merging alone changes
nothing. A new schedule can take up to about 15 minutes to take effect;
the dashboard's Triggers tab shows the active cron.

## Rotation

Before the token expires: generate a replacement with the same settings,
run `npx wrangler secret put GITHUB_TOKEN`, then delete the old token.
