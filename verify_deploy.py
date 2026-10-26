#!/usr/bin/env python3
"""Wait for a Vercel production deployment, and retry it without the build cache.

A git push that lands on the default branch is not the same thing as a build
that shipped. When the build fails, CI often reports success because the push
itself worked, and the change sits unpublished until somebody notices.

This is worse than a plain flake for one specific failure: next/font/google
downloads font data at build time and writes it into Vercel's build cache. If
one of those downloads fails, the broken entry is cached, and every later build
reads the same broken entry and fails identically. A normal redeploy reuses the
cache, so it fails too. The only thing that clears it is a build that skips the
cache, which is what `forceNew=1` does.

Usage:
    verify_deploy.py <project> <commit-sha> [--repo-id N] [--ref main]

Token, in order of precedence: --token, VERCEL_TOKEN, ~/.vercel-token

Exit code 0 means a production deployment for that commit reached READY.
"""
import argparse
import json
import os
import sys
import time
import urllib.request

API = "https://api.vercel.com"
DEFAULT_TOKEN_FILE = os.path.expanduser("~/.vercel-token")


def read_token(explicit: str | None) -> str:
    if explicit:
        return explicit.strip()
    env = os.environ.get("VERCEL_TOKEN")
    if env:
        return env.strip()
    try:
        with open(DEFAULT_TOKEN_FILE) as fh:
            return fh.read().strip()
    except OSError:
        sys.exit(f"no token: pass --token, set VERCEL_TOKEN or write {DEFAULT_TOKEN_FILE}")


class Vercel:
    def __init__(self, token: str, team: str | None = None):
        self.token = token
        self.team = team

    def __call__(self, path: str, data=None):
        if self.team:
            path += ("&" if "?" in path else "?") + "teamId=" + self.team
        req = urllib.request.Request(
            API + path,
            data=json.dumps(data).encode() if data is not None else None,
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
            method="POST" if data is not None else "GET",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode(), strict=False)


def find_deployment(api: Vercel, project: str, sha: str, timeout: int) -> str | None:
    """The GitHub webhook lags, so the deployment may not exist yet."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        found = api(f"/v6/deployments?limit=10&app={project}&target=production")
        for d in found.get("deployments", []):
            if (d.get("meta") or {}).get("githubCommitSha", "").startswith(sha[:7]):
                return d["uid"]
        time.sleep(15)
    return None


def wait_for(api: Vercel, uid: str, timeout: int) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = api(f"/v13/deployments/{uid}").get("readyState", "")
        if state in ("READY", "ERROR", "CANCELED"):
            return state
        time.sleep(15)
    return "TIMEOUT"


def rebuild_without_cache(api: Vercel, project: str, repo_id: int, ref: str) -> str:
    """forceNew skips the build cache. Nothing else clears a poisoned entry."""
    created = api(
        "/v13/deployments?forceNew=1&skipAutoDetectionConfirmation=1",
        {
            "name": project,
            "target": "production",
            "gitSource": {"type": "github", "repoId": repo_id, "ref": ref},
        },
    )
    return created["id"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("project")
    ap.add_argument("sha")
    ap.add_argument("--repo-id", type=int, help="GitHub repo id, needed for the cacheless retry")
    ap.add_argument("--ref", default="main")
    ap.add_argument("--token")
    ap.add_argument("--team", help="Vercel team id, for team-owned projects")
    ap.add_argument("--find-timeout", type=int, default=300)
    ap.add_argument("--build-timeout", type=int, default=900)
    args = ap.parse_args()

    api = Vercel(read_token(args.token), args.team)

    uid = find_deployment(api, args.project, args.sha, args.find_timeout)
    if not uid:
        print(f"no production deployment appeared for {args.sha[:7]}", file=sys.stderr)
        return 1

    state = wait_for(api, uid, args.build_timeout)
    if state == "READY":
        print(f"deployed: {uid}")
        return 0

    if not args.repo_id:
        print(f"deployment {state} ({uid}); pass --repo-id to retry without the cache", file=sys.stderr)
        return 1

    print(f"deployment {state} ({uid}); retrying without the build cache", file=sys.stderr)
    retry = rebuild_without_cache(api, args.project, args.repo_id, args.ref)
    state = wait_for(api, retry, args.build_timeout)
    if state == "READY":
        print(f"deployed on the cacheless retry: {retry}")
        return 0

    print(f"cacheless retry also {state} ({retry})", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
