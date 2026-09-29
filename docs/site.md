# Events website (Cloudflare Pages)

`site/` is a static page plus one Pages Function:

- `site/public/`: the page and a 404 page (no build step, no external
  assets). Event data is inserted as text only, never as HTML, and
  `_headers` sets a strict Content Security Policy.
- `site/functions/v1/[[path]].js`: serves `/v1/...` feed files from the
  public R2 bucket through a binding, same-origin with the page. Only
  published file names are served.

## One-time setup (Cloudflare Students account)

1. **Create the project.** Workers & Pages -> Create -> Pages -> Connect to
   Git -> authorize the Cloudflare GitHub app for **only** the
   `eventradar` repository -> select it.
   - Production branch: `main`
   - Framework preset: None
   - Build command: *(empty)*
   - Build output directory: `public`
   - Root directory: `site`
2. **Bind the bucket, Production only.** Project -> Settings (environment:
   Production) -> Bindings -> Add -> R2 bucket: variable name
   `FEED_BUCKET`, bucket = the public feed bucket. Do not add it to
   Preview: an R2 binding can write, and preview deployments run code from
   unmerged branches.
3. **Limit preview builds.** Settings -> Build -> Branch control -> Preview
   branch: None (or named branches only).
4. **Redeploy** so the binding applies: Deployments -> latest production
   deployment -> Retry deployment.
5. **Check** `https://<project>.pages.dev` and
   `https://<project>.pages.dev/v1/socal-tech/manifest.json`.

## The page

- Search (title, venue, address, organizer), kind chips, a "New this
  week" toggle, and a page size of 10, 25 (default), 50, or all.
- The view is kept in the URL so it can be shared: `q`, `kind`,
  `new=1`, `show` (`10`, `50`, `all`), `page`, and `profile` (another
  published profile; default `socal-tech`).
- Subscribe links point at the profile's `.ics`, RSS, and JSON feeds.

## Custom domain from another Cloudflare account (optional)

The domain's zone lives on a different Cloudflare account than the
project, so that account acts as a plain DNS provider:

1. In the **project** (Students account): Custom domains -> Set up a
   custom domain -> enter the subdomain (e.g. `events.example.com`).
   Do this first; a CNAME created before this step fails with 522.
2. In the **zone's** account: DNS -> Add record -> `CNAME`, name
   `events`, target `<project>.pages.dev`, **Proxy status: DNS only**
   (grey cloud). A proxied record pointing at another account fails with
   error 1014.
3. Back in the project, wait for the domain to show **Active** (HTTPS is
   issued automatically).

## Afterwards

Once the site works, the bucket's public `r2.dev` access is no longer
needed: R2 -> bucket -> Settings -> Public access -> disable. The page,
feeds, and API are then served only through the site.

## Local preview

```bash
uv run eventradar run
EVENTRADAR_DATA_DIR=.eventradar/public uv run python scripts/serve_site.py 8788
```

`run` (the `local` environment) writes feeds to `.eventradar/public`;
`--no-publish` would skip that. Then open http://localhost:8788.

Tests: `node --test site/tests/*.test.mjs`
