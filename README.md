# vercel-deploy-verify

Wait for a Vercel production deployment to finish, and retry it **without the
build cache** when it fails.

```sh
verify_deploy.py my-project "$(git rev-parse HEAD)" --repo-id 123456789
```

Exit code 0 means a production deployment for that commit reached `READY`.
Anything else means the change is not live, which is the thing a `git push`
cannot tell you.

## The failure this was written for

```
Module not found: Can't resolve
  '@vercel/turbopack-next/internal/font/google/font?{"url":"https://fonts.gs...'

Error while looking up import map: next/font/google queries have exactly one entry
  - Execution of <NextFontGoogleFontFileReplacer as ImportMappingReplacement>::result failed
  - next/font/google queries have exactly one entry

Error: Turbopack build failed with 8 errors
```

If you have hit this, you have probably also noticed that it does not behave
like a normal flake:

- the same commit builds fine locally, even after `rm -rf .next`
- **redeploying the same commit fails again, every time**
- a different font name appears in the error on different days

### Why

`next/font/google` fetches font data from Google **at build time** and writes
the result into Vercel's build cache. When one of those fetches fails, the
broken entry is cached. Every later build reads the same broken entry, so the
failure stops being intermittent and becomes permanent for that project. A
normal redeploy reuses the cache, which is why retrying never helps.

The only thing that clears it is a build that skips the cache. On the API that
is `forceNew=1`; in the dashboard it is the Redeploy dialog with **Use existing
build cache** unticked.

### Two fixes, and you want both

**Immediate, no code change.** Retry without the cache. That is what this
script automates, so a failed build repairs itself instead of silently leaving
your change unpublished.

**Permanent.** Remove the build-time network dependency: self-host the fonts
with `next/font/local` instead of `next/font/google`. Download the same woff2
files Google serves, keep each subset's `unicode-range`, and the build stops
touching the network at all.

```ts
// Google's own file, Google's own unicode-range, served from your origin.
export const serif = localFont({
  src: [
    { path: "./fonts/newsreader-latin-normal.woff2", style: "normal" },
    { path: "./fonts/newsreader-latin-italic.woff2", style: "italic" },
  ],
  weight: "400 500",
  variable: "--font-serif",
  display: "swap",
  declarations: [{ prop: "unicode-range", value: "U+0000-00FF, U+0131, ..." }],
});
```

Two things that cost us time and are easy to get wrong:

- `next/font/google` requests **static weight instances**, not the variable
  font. Download the variable font instead and your line breaks shift and the
  payload grows. Ask Google for `wght@400;500`, not `wght@400..500`.
- `next/font` rejects a variable where it expects a literal, so the
  `unicode-range` string has to be written out at each call site.
- Set `preload: false` on subsets that are not needed on every page, or the
  Arabic face downloads on your English pages.

## Install

No dependencies beyond Python 3.10+.

```sh
curl -fsSLO https://raw.githubusercontent.com/ilhanbugraaslan/vercel-deploy-verify/main/verify_deploy.py
chmod +x verify_deploy.py
```

## Token

In order of precedence: `--token`, `VERCEL_TOKEN`, `~/.vercel-token`.
A token with deployment read and write access is enough.

## Options

| Flag | Meaning |
|---|---|
| `--repo-id` | GitHub repository id. Required for the cacheless retry. |
| `--ref` | Branch for the retry. Default `main`. |
| `--team` | Vercel team id, for team-owned projects. |
| `--find-timeout` | Seconds to wait for the deployment to appear. Default 300. |
| `--build-timeout` | Seconds to wait for a build to settle. Default 900. |

Find the repo id with:

```sh
gh api repos/OWNER/REPO --jq .id
```

## In a publishing pipeline

The reason this exists: an automated job committed a post, pushed it, reported
success, and the post stayed 404 for a day because the build failed. Verifying
the push is not verifying the deploy.

```sh
git push origin main || exit 1
verify_deploy.py my-project "$(git rev-parse HEAD)" --repo-id 123456789 \
  || { echo "pushed but not deployed" >&2; exit 1; }
```

## License

MIT. See [LICENSE](LICENSE).

## Who maintains this

Built and maintained by [wedevit](https://wedevit.net), an independent IT team
that builds and runs infrastructure: servers, networks, firewalls, CI and the
software on top of it. We resell no hardware and take no vendor commission.
