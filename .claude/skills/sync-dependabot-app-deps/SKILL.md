---
name: sync-dependabot-app-deps
description: Read every open Dependabot PR for an application-code dependency (Python pip/uv and JS npm/yarn/pnpm) and carry each version bump over to the local dependency files (requirements.txt, pyproject.toml, uv.lock, package.json, package-lock.json/yarn.lock/pnpm-lock.yaml) without merging the PRs.
allowed-tools: Read, Bash, Edit
---

# Sync Dependabot App-Dependency Updates

Dependabot opens one PR per application-code dependency bump across several
directories (Python: `/`, `/oss-crs-infra/builder-sidecar`,
`/oss-crs-infra/runner-sidecar`, `/oss-crs-infra/lifecycle`,
`/oss-crs-infra/litellm-key-gen`; JS: `/site` and any other npm package — see
`.github/dependabot.yml`). This skill collects all the **open Python and JS
PRs** and applies their changes directly to the local working tree, so the
bumps land in one local change set instead of N separate merges.

The reliable signal for "this PR is an app-dep bump" is the branch name prefix:
- Python (pip): `dependabot/pip/` — used for plain `requirements.txt` directories
- Python (uv): `dependabot/uv/` — used for the root project that ships a `uv.lock`
- JS (npm / yarn / pnpm): `dependabot/npm_and_yarn/`

Both Python prefixes must be matched. Dependabot picks `uv/` over `pip/` for any
directory where it detects a `uv.lock`, regardless of how the directory is
declared in `.github/dependabot.yml`. Missing `dependabot/uv/` silently drops
the root project's PRs.

GitHub Actions (`dependabot/github_actions/`) and Docker (`dependabot/docker/`)
PRs are **excluded** — leave them alone (Docker has its own skill,
`sync-dependabot-pins`).

## Step 0 — Start from an up-to-date main on a fresh branch

Make sure the working tree is clean first (`git status --short`); if there are
uncommitted changes, stop and ask the user how to proceed rather than pulling
over them. Then update `main` and cut a dated working branch so the carried-over
bumps land in one reviewable branch:

```bash
git checkout main
git pull
git checkout -b "chore/app-deps-dependabot-$(date +%Y-%m-%d)"
```

If the branch already exists (the skill was run earlier today), switch to it
instead: `git checkout "chore/app-deps-dependabot-$(date +%Y-%m-%d)"`.

## Step 1 — Enumerate the open Python and JS Dependabot PRs

```bash
gh pr list --author "app/dependabot" --state open --limit 100 \
  --json number,title,headRefName \
  --jq '.[] | select(.headRefName | startswith("dependabot/pip/") or startswith("dependabot/uv/") or startswith("dependabot/npm_and_yarn/")) | "\(.number)\t\(.headRefName)\t\(.title)"'
```

If this prints nothing, there are no open Python or JS Dependabot PRs — stop
and tell the user there is nothing to sync.

Otherwise, list the matched PR numbers to the user before applying so they know
what is about to change. It is helpful to group them by ecosystem (pip vs
npm_and_yarn) in the report.

## Step 2 — Apply each PR's diff to the local working tree

For each matched PR number `N`, apply its diff with a 3-way merge. `--3way` lets
two PRs that touch the **same** file (e.g. one bumps `uvicorn`, another bumps
`python-multipart` in the same `requirements.txt`; or two npm bumps that both
touch `package-lock.json`) both land cleanly, and it correctly carries
generated lockfile changes (`uv.lock`, `package-lock.json`, `yarn.lock`,
`pnpm-lock.yaml`) that cannot be hand-edited:

```bash
for N in <pr numbers, space separated>; do
  echo "=== PR $N ==="
  if gh pr diff "$N" 2>/dev/null | git apply --3way; then
    echo "PR $N applied"
  else
    echo "PR $N did NOT apply cleanly — handle manually (see Step 3)"
  fi
done
```

Apply them one at a time in the same loop so each result is visible. A PR that
applies cleanly prints `Applied patch ... cleanly` (or nothing on success with
plain `git apply`); a failure is reported explicitly.

## Step 3 — Fallback for any PR that does not apply cleanly

`git apply` can fail when the local file has already diverged from the PR's
base (for example, an earlier PR in this same run already changed an adjacent
line in a way the 3-way merge could not reconcile, or the PR's lockfile diff
references hashes that no longer match). For each such PR:

1. Read the diff to see exactly what changed:
   ```bash
   gh pr diff <N>
   ```
2. The change is almost always a single dependency constraint, e.g.
   `uvicorn>=0.48.0` → `uvicorn>=0.49.0` (Python) or
   `"react": "^18.3.0"` → `"react": "^18.4.0"` (JS). Open the target file
   named in the diff header (`+++ b/<path>`) and apply the same edit by hand
   with the Edit tool.
3. After editing the manifest, regenerate the matching lockfile rather than
   editing it by hand. Pick the tool that matches the lockfile already present
   in that directory — do not introduce a different package manager:
   - Python `pyproject.toml` next to `uv.lock` → `uv lock`
   - npm `package.json` next to `package-lock.json` →
     `npm install --package-lock-only --prefix <dir>`
   - yarn `package.json` next to `yarn.lock` →
     `yarn install --cwd <dir>`
   - pnpm `package.json` next to `pnpm-lock.yaml` →
     `pnpm install --dir <dir> --lockfile-only`

## Step 4 — Verify the result

`git apply --3way` stages successful merges, so plain `git diff` will look
empty. Use `--cached` to see the changes:

```bash
git status --short
git diff --cached --stat
git diff --cached -- '*requirements.txt' 'pyproject.toml' '**/package.json'
```

Every old version constraint from a matched PR should now show as the new
version in the staged diff. Lockfile churn (`uv.lock`, `package-lock.json`,
`yarn.lock`, `pnpm-lock.yaml`) will also appear and is expected. Report any PR
whose change is missing.

## Step 5 — Report

Summarize what was carried over. Do **not** commit, push, or merge unless the
user asks — leave the changes staged in the working tree for their review.
Group by ecosystem so the user can copy `Closes #N` lines into the eventual PR
body:

```
Synced 5 app-dep Dependabot PRs into the working tree:

Python (pip):
  #271  fastapi           >=0.136.3 → >=0.137.1   oss-crs-infra/builder-sidecar/requirements.txt
  #270  fastapi           >=0.136.3 → >=0.137.1   oss-crs-infra/runner-sidecar/requirements.txt
  #266  python-multipart  >=0.0.30  → >=0.0.32    oss-crs-infra/runner-sidecar/requirements.txt
  #265  python-multipart  >=0.0.30  → >=0.0.32    oss-crs-infra/builder-sidecar/requirements.txt

JS (npm_and_yarn):
  #284  @docusaurus/core  ^3.7.0 → ^3.8.0         site/package.json (+ package-lock.json)

Files changed: 2 requirements.txt, 1 package.json, 1 package-lock.json
Not applied: (none)
```

Mention any PRs that needed the Step 3 fallback, and any that still did not
apply so the user can resolve them manually.
