---
name: sync-dependabot-pins
description: After a Dependabot bump of oss-crs-infra/dependabot-pins.Dockerfile, resolve the human-readable version tag for each updated digest, fix the inline comment, and sync the digest to oss_crs/src/constants.py.
allowed-tools: Read, Bash, Edit
---

# Sync Dependabot Pins

Dependabot updates image digests in `oss-crs-infra/dependabot-pins.Dockerfile` but leaves version comments stale or copies the old one. This skill resolves the correct human-readable version for each changed digest, patches the Dockerfile comment, and syncs the digest to `oss_crs/src/constants.py`.

## Step 1 — Confirm the latest commit touched dependabot-pins.Dockerfile

```bash
git log --oneline -1 -- oss-crs-infra/dependabot-pins.Dockerfile
```

If no output, the latest commit did **not** touch this file. Stop here and tell the user there is nothing to sync.

## Step 2 — Read the current Dockerfile

Read `oss-crs-infra/dependabot-pins.Dockerfile` and extract every `FROM` line. Each has the form:

```
FROM <image>@sha256:<digest>  # <version-comment>
```

Note the image name, full digest, and existing comment for each line.

## Step 3 — Identify what changed

```bash
git diff HEAD~1 HEAD -- oss-crs-infra/dependabot-pins.Dockerfile
```

Only process the `FROM` lines whose digest actually changed. Leave unchanged lines alone.

## Step 4 — Resolve the human-readable version for each changed digest

For each changed image, use `docker buildx imagetools inspect` to scan release tags until the digest matches. This avoids pulling the image and works for multi-platform indexes.

### For `ghcr.io/berriai/litellm-database`

The image uses semver tags (`v1.86.1`, `v1.87.0`, etc.) that match the `BerriAI/litellm` GitHub release tags. Fetch recent releases and resolve each one's digest:

```bash
TARGET_DIGEST="sha256:<new-digest>"

# Get recent release tags from GitHub
TAGS=$(curl -s "https://api.github.com/repos/BerriAI/litellm/releases?per_page=20" \
  | python3 -c "import json,sys; [print(r['tag_name']) for r in json.load(sys.stdin) if not r.get('prerelease')]")

# Resolve each tag to its OCI index digest and look for a match
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

If the matching tag is not in the first 20 releases, broaden the search with `per_page=50` or check older pages.

### For `postgres` (Docker Hub)

```bash
TARGET_DIGEST="sha256:<new-digest>"

# Fetch recent postgres tags and resolve each
curl -s "https://registry.hub.docker.com/v2/repositories/library/postgres/tags?page_size=50&ordering=last_updated" \
  | python3 -c "
import json,sys
data=json.load(sys.stdin)
for t in data.get('results',[]):
    # Skip non-numeric tags (alpha, beta, etc.)
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
rate-limits anonymous manifest inspects, often hitting newer tags like the
`18.x` series while older tags still resolve), skip buildx entirely and match
against the digests already embedded in the Docker Hub tags API response. Each
tag entry carries a top-level `digest` (the multi-arch OCI index digest, which
is what the `FROM ...@sha256:` pin uses) plus per-`images` digests:

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

This usually returns several aliases for one digest (e.g. `18.4`, `18`,
`trixie`, `latest`). Pick the most specific numeric version tag (`18.4`) for the
comment. Note that a digest bump does **not** always mean a version bump — a
rebuild of the same version with refreshed base packages keeps the same numeric
tag, so the existing comment may already be correct.

### Fallback — Inspect the Dependabot commit message

The commit message from `git log -1 -- oss-crs-infra/dependabot-pins.Dockerfile` sometimes contains release note links. You can also check GHCR tags directly:

```bash
TOKEN=$(curl -s "https://ghcr.io/token?service=ghcr.io&scope=repository:berriai/litellm-database:pull" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["token"])')
curl -s -H "Authorization: Bearer $TOKEN" "https://ghcr.io/v2/berriai/litellm-database/tags/list" \
  | python3 -c "import json,sys; print('\n'.join(data.get('tags',[]) for data in [json.load(sys.stdin)]))"
```

If no strategy resolves the version, leave the comment as `# <unknown>` and note it in your response so the user can fill it in manually.

## Step 5 — Update the inline comment in dependabot-pins.Dockerfile

For each changed `FROM` line, replace the old comment with the resolved version:

```
FROM <image>@sha256:<new-digest>  # <resolved-version>
```

Use the Edit tool to make this change.

## Step 6 — Sync digests to oss_crs/src/constants.py

Read `oss_crs/src/constants.py`. It contains constants of the form:

```python
LITELLM_IMAGE = "ghcr.io/berriai/litellm-database@sha256:<digest>"  # <version>
POSTGRES_IMAGE = "postgres@sha256:<digest>"  # <version>
```

The mapping from Dockerfile `FROM` image to Python constant is:

| Dockerfile image prefix | Python constant |
|------------------------|-----------------|
| `ghcr.io/berriai/litellm-database` | `LITELLM_IMAGE` |
| `postgres` | `POSTGRES_IMAGE` |

For each changed image, update **both** the digest and the inline comment in constants.py to match what is now in dependabot-pins.Dockerfile. Use the Edit tool.

## Step 7 — Verify the sync

After editing, confirm the digests and comments match between the two files:

```bash
grep -E 'sha256:|# v|# [0-9]' oss-crs-infra/dependabot-pins.Dockerfile oss_crs/src/constants.py
```

All digests that appear in dependabot-pins.Dockerfile must appear verbatim in constants.py. Report any mismatch.

## Example Output

After a successful sync, report something like:

```
Updated: ghcr.io/berriai/litellm-database
  Old digest: 069da88...  # v1.84.1
  New digest: 49f8919...  # v1.85.0
  Updated in: oss-crs-infra/dependabot-pins.Dockerfile, oss_crs/src/constants.py
```
