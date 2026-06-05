---
name: sync-dependabot-pins
description: Given one or more Dependabot PRs that bump image digests in oss-crs-infra/dependabot-pins.Dockerfile, resolve the human-readable version tag for each changed digest, fix the inline Dockerfile comment, and sync the digest + comment into oss_crs/src/constants.py — committing the fix onto each PR's own branch.
---

# Sync Dependabot Pins

Dependabot updates image digests in `oss-crs-infra/dependabot-pins.Dockerfile` but
leaves the inline version comments stale (it copies the old comment). The actual
refs are also mirrored in `oss_crs/src/constants.py`, which Dependabot does **not**
touch. Your job, for each Dependabot PR you are given, is to:

1. Resolve the correct human-readable version for every changed digest.
2. Fix the inline `# <version>` comment in the Dockerfile.
3. Sync the digest **and** comment into `oss_crs/src/constants.py`.
4. Commit the fix onto **that PR's branch** so it rides along with the bump.

## Inputs

You are given **one or more pull requests** (by number or URL) in the prompt — each
a Dependabot bump against the docker pin. Collect the PR numbers. If no PR is named
and you are already running on a PR, operate on the current PR only.

Process each PR **independently**, start to finish, before moving to the next.

## For each PR

### Step 1 — Check out the PR branch

```bash
gh pr checkout <PR_NUMBER>
```

(Skip if you are already on that PR's branch.) All edits and the commit for this PR
must land on this branch.

### Step 2 — Identify what changed

Look only at the `FROM` lines whose digest actually changed in this PR:

```bash
gh pr diff <PR_NUMBER> -- oss-crs-infra/dependabot-pins.Dockerfile
```

Each `FROM` line has the form:

```
FROM <image>@sha256:<digest>  # <version-comment>
```

Note the image name and the **new** digest for every changed line. Leave unchanged
lines alone.

### Step 3 — Resolve the human-readable version for each changed digest

Use `docker buildx imagetools inspect` to scan release tags until the digest matches.
This avoids pulling the image and works for multi-platform indexes.

#### For `ghcr.io/berriai/litellm-database`

Tags are semver (`v1.86.1`, `v1.87.0`, …) matching `BerriAI/litellm` GitHub release tags:

```bash
TARGET_DIGEST="sha256:<new-digest>"

TAGS=$(curl -s "https://api.github.com/repos/BerriAI/litellm/releases?per_page=20" \
  | python3 -c "import json,sys; [print(r['tag_name']) for r in json.load(sys.stdin) if not r.get('prerelease')]")

for tag in $TAGS; do
  DIGEST=$(docker buildx imagetools inspect "ghcr.io/berriai/litellm-database:$tag" \
    --format '{{.Manifest.Digest}}' 2>/dev/null)
  echo "$tag -> $DIGEST"
  if [ "$DIGEST" = "$TARGET_DIGEST" ]; then
    echo "MATCH FOUND: $tag"
    break
  fi
done
```

If the matching tag is not in the first 20 releases, broaden with `per_page=50` or
check older pages.

#### For `postgres` (Docker Hub)

```bash
TARGET_DIGEST="sha256:<new-digest>"

curl -s "https://registry.hub.docker.com/v2/repositories/library/postgres/tags?page_size=50&ordering=last_updated" \
  | python3 -c "
import json,sys
data=json.load(sys.stdin)
for t in data.get('results',[]):
    name=t['name']
    if name[0].isdigit():
        print(name)
" | while read tag; do
  DIGEST=$(docker buildx imagetools inspect "postgres:$tag" \
    --format '{{.Manifest.Digest}}' 2>/dev/null)
  if [ "$DIGEST" = "$TARGET_DIGEST" ]; then
    echo "MATCH FOUND: $tag"
    break
  fi
done
```

**If `docker buildx imagetools inspect` returns empty digests** (Docker Hub
rate-limits anonymous manifest inspects, often hitting newer tags like the `18.x`
series while older tags still resolve), skip buildx and match against the digests
already embedded in the Docker Hub tags API response. Each tag entry carries a
top-level `digest` (the multi-arch OCI index digest — what the `FROM ...@sha256:`
pin uses) plus per-`images` digests:

```bash
TARGET_DIGEST="sha256:<new-digest>"

for page in 1 2 3; do
curl -s "https://registry.hub.docker.com/v2/repositories/library/postgres/tags?page_size=100&page=$page&ordering=last_updated" \
  | python3 -c "
import json,sys
target='$TARGET_DIGEST'
data=json.load(sys.stdin)
for t in data.get('results',[]):
    if t.get('digest','')==target:
        print('MATCH (index digest):', t['name'])
    for img in t.get('images',[]):
        if img.get('digest','')==target:
            print('MATCH (image digest):', t['name'], img.get('os'), img.get('architecture'))
"
done
```

This usually returns several aliases for one digest (e.g. `18.4`, `18`, `trixie`,
`latest`). Pick the most specific numeric version tag (`18.4`) for the comment. Note
a digest bump does **not** always mean a version bump — a rebuild of the same version
with refreshed base packages keeps the same numeric tag, so the existing comment may
already be correct.

#### Fallback — inspect the Dependabot PR / GHCR tags

The PR description and commit message sometimes contain release-note links
(`gh pr view <PR_NUMBER>`). You can also list GHCR tags directly:

```bash
TOKEN=$(curl -s "https://ghcr.io/token?service=ghcr.io&scope=repository:berriai/litellm-database:pull" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["token"])')
curl -s -H "Authorization: Bearer $TOKEN" "https://ghcr.io/v2/berriai/litellm-database/tags/list" \
  | python3 -c "import json,sys; print('\n'.join(data.get('tags',[]) for data in [json.load(sys.stdin)]))"
```

If no strategy resolves the version, leave the comment as `# <unknown>` and call it
out in your PR comment so a human can fill it in.

### Step 4 — Update the inline comment in dependabot-pins.Dockerfile

For each changed `FROM` line, replace the stale comment with the resolved version:

```
FROM <image>@sha256:<new-digest>  # <resolved-version>
```

### Step 5 — Sync digests to oss_crs/src/constants.py

`oss_crs/src/constants.py` holds constants of the form:

```python
LITELLM_IMAGE = "ghcr.io/berriai/litellm-database@sha256:<digest>"  # <version>
POSTGRES_IMAGE = "postgres@sha256:<digest>"  # <version>
```

Mapping from Dockerfile image to Python constant:

| Dockerfile image prefix              | Python constant  |
|--------------------------------------|------------------|
| `ghcr.io/berriai/litellm-database`   | `LITELLM_IMAGE`  |
| `postgres`                           | `POSTGRES_IMAGE` |

For each changed image, update **both** the digest and the inline comment in
`constants.py` to match what is now in the Dockerfile.

### Step 6 — Verify the sync

```bash
grep -E 'sha256:|# v|# [0-9]' oss-crs-infra/dependabot-pins.Dockerfile oss_crs/src/constants.py
```

Every digest in the Dockerfile must appear verbatim in `constants.py`. Report any
mismatch.

### Step 7 — Commit onto the PR branch

```bash
git add oss-crs-infra/dependabot-pins.Dockerfile oss_crs/src/constants.py
git commit -m "chore: resolve version comment and sync digest to constants.py"
git push
```

The commit lands on the Dependabot PR's branch, so the fix merges together with the
bump.

## Per-PR report

After each PR, summarize:

```
PR #<num> — ghcr.io/berriai/litellm-database
  Old digest: 069da88...  # v1.84.1
  New digest: 49f8919...  # v1.85.0
  Synced in:  oss-crs-infra/dependabot-pins.Dockerfile, oss_crs/src/constants.py
  Pushed to:  <branch>
```

When all provided PRs are processed, give a one-line roll-up of which PRs were synced
and any that need manual attention (unresolved version, no digest change, etc.).
