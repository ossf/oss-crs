# Integrating OSS-CRS with GitLab CI/CD: Lessons Learned

This document captures everything learned building a working GitLab CI/CD
pipeline around [OSS-CRS](https://github.com/ossf/oss-crs), from initial
setup through a fully automated commit → finding → patch → merge-request-comment
loop. It's written for anyone attempting the same integration — especially
against **self-hosted GitLab** on modest hardware, which is where most of
the non-obvious problems live.

## Architecture: what "GitLab integration" actually means

OSS-CRS is a standalone CLI (`prepare`, `build-target`, `run`, `artifacts`).
It has **no GitHub- or GitLab-specific code anywhere** — their own CI
(`.github/workflows/ci.yml`) is just GitHub Actions steps that happen to
call the same `uv run oss-crs ...` commands you'd run by hand. "GitLab
integration" is therefore not a code feature to build inside OSS-CRS; it's
an **operational recipe**: which commands to run, in what order, with what
environment prerequisites, packaged so someone else can reproduce it without
hitting the same walls.

The pipeline built here has two jobs:
- `crs-scan`: runs a bug-finding CRS (`crs-libfuzzer`) against the target,
  publishes any crash (POV) as a pipeline artifact.
- `crs-patch`: if a POV exists, runs a patching CRS (`crs-claude-code`)
  against it, publishes the resulting patch as an artifact **and** posts it
  as a comment on the merge request.

## Prerequisite: Docker-outside-of-Docker (DooD)

OSS-CRS itself runs `docker build` / `docker compose` internally. Since your
GitLab Runner's job containers are themselves Docker containers, they need
access to a Docker daemon to spawn *more* containers. The standard approach
is **Docker-outside-of-Docker**: mount the host's `/var/run/docker.sock`
into job containers, so `docker` commands inside a job talk to the same
daemon as the host, creating *sibling* containers rather than nested ones.

```toml
# gitlab-runner config.toml, per [[runners]] entry
[runners.docker]
  volumes = ["/cache", "/var/run/docker.sock:/var/run/docker.sock"]
```

This is simpler than full Docker-in-Docker (no privileged mode, no nested
daemon) but introduces a whole category of bugs below, all stemming from one
fact: **a path that exists inside your job container may not exist on the
host**, and DooD-created sibling containers are built by the *host* daemon,
which only knows about *host* paths.

## Lesson 1: DooD path mismatches break bind mounts silently

**Symptom:** `build-target` reports success (compiler output looks fine,
image builds), but the subsequent "Check outputs" step fails to find the
build artifacts. Or: `docker compose up` fails with
`invalid mount config for type "bind": bind source path does not exist`.

**Root cause:** OSS-CRS's own working directory (`.oss-crs-workdir`) and any
files it generates get created inside the job container's filesystem. When
it then tells the host Docker daemon to bind-mount one of those paths into a
sibling container, the daemon looks for that path *on the host* — where it
doesn't exist — and either silently creates an empty directory there (data
vanishes) or fails outright.

**Fix:** Give job containers a directory that's **actually shared** with the
host at an identical path, and make sure OSS-CRS's working directory lives
under it.

```toml
volumes = ["/cache", "/var/run/docker.sock:/var/run/docker.sock", "/builds:/builds", "/tmp:/tmp"]
```

`/builds` is already where GitLab Runner checks out your repo
(`$CI_PROJECT_DIR`), so cloning OSS-CRS there instead of `/tmp` fixes most
cases:

```bash
git clone https://github.com/ossf/oss-crs.git "$CI_PROJECT_DIR/.oss-crs-tmp"
```

`/tmp` needed its own mount separately — OSS-CRS's `crs-claude-code` compose
setup writes a `postgres_password` secret file directly under Python's
default `tempfile` location (`/tmp/<random>/postgres_password`), bypassing
`TMPDIR` entirely. Sharing `/tmp` wholesale was the only reliable fix found;
setting `TMPDIR` in the job environment did *not* redirect this specific
file.

## Lesson 2: `gitlab` hostname resolution inside job containers

**Symptom:** git clone fails inside a job with
`Could not resolve host: gitlab`, or (before that fix) job containers try to
clone via `http://localhost:8929/...` and get `Could not connect to server`.

**Root cause:** GitLab's `external_url` is set to `http://localhost:8929`
for *your browser's* benefit (reached through an SSH tunnel or port
forward). Inside a job container, `localhost` refers to the job container
itself, not the GitLab server.

**Fix, two parts:**
1. Put the runner's job containers on the same Docker network as the GitLab
   container (`network_mode` in `config.toml`, or `--docker-network-mode` at
   registration), so the hostname `gitlab` resolves via Docker's embedded
   DNS.
2. Set **Admin Area → Settings → General → Visibility and access controls →
   "Custom Git clone URL for HTTP(S)"** to `http://gitlab:8929`, so
   GitLab itself tells job containers to clone via the internal hostname
   instead of the browser-facing `external_url`.

This same class of bug recurred later for any API call made *from inside a
job* back to GitLab's own API — `CI_API_V4_URL` is also built from
`external_url` and resolves to `localhost` inside a job. Any `curl` call
back to GitLab's API from a job needs `http://gitlab:8929/api/v4/...`
hardcoded, not the predefined variable.

## Lesson 3: stale Docker image caching hides real changes

**Symptom:** You push a commit that changes the vulnerable source code, the
pipeline runs, `crs-scan` reports zero findings — even though the bug is
still there.

**Root cause:** `build-target` checks whether an image with the target's
computed tag already exists locally and skips rebuilding if so. In CI, the
Docker daemon persists across job runs (it's the *host's* daemon under
DooD), so a stale image from an earlier run — built from old source — gets
silently reused.

**Fix:** explicitly remove any cached image for the target before every
build, forcing a fresh build every run:

```bash
docker images --filter "reference=<project-name>" -q | xargs -r docker rmi -f
```

## Lesson 4: `litellm`'s healthcheck can time out before it's actually ready

**Symptom:** `docker compose up` fails with
`dependency failed to start: container oss-crs-litellm-1 is unhealthy`,
despite `litellm` genuinely still starting (not crashed, not resource-starved
— confirmed via `docker stats` showing ample headroom).

**Root cause:** by default, `litellm` fetches a remote model-cost map at
startup. In a DooD CI environment with extra network hops, this can be slow
enough to blow past the container's healthcheck budget
(`start_period: 90s` + `retries: 10` × `interval: 5s` ≈ 140 seconds).

**Fix / workaround:** pass `--offline` to `oss-crs run` and `build-target`.
This sets `LITELLM_LOCAL_MODEL_COST_MAP=True`, skipping the slow fetch
entirely, without disabling the actual LLM API calls (those still work
normally). This was filed as an upstream issue against `ossf/oss-crs`
(healthcheck `start_period`/`retries` too tight for real-world litellm
startup latency in DooD-based CI environments), with the exact template
line/values and before/after timing evidence included in the issue itself.

## Lesson 5: GitLab CE's own memory footprint competes with your CI jobs

**Symptom:** `litellm` and other sidecars fail to start reliably, `docker
stats` shows plenty of *their* headroom, but the host is swapping heavily.

**Root cause:** self-hosted GitLab CE bundles Postgres, Redis, Sidekiq,
Gitaly, Puma, and a full Prometheus monitoring stack (exporters,
Alertmanager) by default — commonly using **10GB+ RAM** on its own. On a
15GB host, that leaves almost nothing for CI work.

**Fix:** trim GitLab's own footprint for small/resource-constrained
deployments:

```ruby
# gitlab.rb / GITLAB_OMNIBUS_CONFIG
prometheus_monitoring['enable'] = false   # biggest single win
puma['worker_processes'] = 2
sidekiq['max_concurrency'] = 10
gitlab_kas['enable'] = false
```

This dropped GitLab's own usage from ~10GB to ~2.3GB in testing — a larger
effect than reducing the CRS compose file's own memory requests (also worth
doing: the example `crs-claude-code` config requests `8G` infra + `16G`
patcher = 24GB, unrealistic for most personal/small hardware; `3G` + `6G`
worked fine).

## Lesson 6: `ANTHROPIC_API_KEY` must be unprotected for MR pipelines

**Symptom:** `crs-patch` fails validating required LLM environment
variables, but the same variable works fine on pushes to the default
branch.

**Root cause:** GitLab CI/CD variables marked **Protected** are only
injected into pipelines where *both* the source and target branch are
protected. Your MR's target (`main`) is usually protected by default; a
feature branch usually isn't.

**Fix:** uncheck "Protected" on any variable needed by MR pipelines running
against unprotected branches (keep "Masked" checked).

## Lesson 7: `CI_JOB_TOKEN` may not have the scope you expect

**Symptom:** posting a comment to a merge request via
`JOB-TOKEN: ${CI_JOB_TOKEN}` returns `401 Unauthorized`, even though the
project's job-token allowlist includes the project itself and the
authentication log shows a successful auth event.

**Root cause:** unclear precisely — likely a permission/scope check beyond
basic authentication that this GitLab version enforces for job tokens on
this endpoint.

**Fix:** use a dedicated **Project Access Token** (Settings → Access
tokens, `Developer` role, `api` scope) stored as a masked, unprotected CI/CD
variable, authenticated via `PRIVATE-TOKEN` instead of `JOB-TOKEN`. More
predictable, and the standard approach for CI-to-API calls that need
guaranteed scope.

## Lesson 8: YAML footguns when writing CI scripts

Several real, reproducible bugs hit while writing this pipeline's bash:

- **Unquoted colons split YAML mappings.** A script line like
  `- echo "Using POV: $POV_FILE"` gets parsed as a *mapping* (key
  `echo "Using POV`, value `$POV_FILE"`), not a string, because YAML treats
  `: ` (colon-space) inside an unquoted plain scalar as a key/value
  separator. Fix: wrap the whole line in single quotes:
  `- 'echo "Using POV: $POV_FILE"'`.
- **Heredocs conflict with YAML block-scalar indentation.** Bash heredocs
  require their closing delimiter at column 0; YAML block scalars (`- |`)
  require every line to maintain at least the block's baseline indentation.
  These two rules are incompatible for any heredoc embedded inside a
  multi-line YAML script block. Fix: avoid heredocs inside `.gitlab-ci.yml`
  entirely. For embedding a larger script (e.g. a Python program), base64-encode
  it into a single line with no newlines:
  `echo '<base64>' | base64 -d | python3 -` — sidesteps both the
  indentation conflict and any quoting issues, since the base64 alphabet
  contains no colons, quotes, or other YAML-significant characters.
- **`find | head -1` under `pipefail` can SIGPIPE.** If a directory has many
  matching files, `find` writing to a pipe that `head -1` closes early after
  one line causes `find` to receive SIGPIPE (exit 141). Under `pipefail`,
  this fails the whole line. Fix: `find ... -print -quit` instead — `find`
  itself stops after the first match, no pipe involved.

## Lesson 9: capturing logs from ephemeral containers

OSS-CRS cleans up its own Docker Compose stack immediately after a run
(`docker compose down -v --rmi local --remove-orphans`), often within
seconds of a container exiting — faster than a naive "check after the
command returns" approach can react.

**What worked:** a background polling loop, started *before* the main
`oss-crs run` command, that watches for the target container by name and
repeatedly `docker cp`s the relevant path out (not stopping at the first
successful copy — the file may still be empty at that point; keep
overwriting until the container disappears).

**A specific trap:** the path OSS-CRS's own log messages reference
(`/work/agent/claude_stderr.log`) is a **symlink** to the real location
(`/OSS_CRS_LOG_DIR/agent/...`). `docker cp` does not follow symlinks by
default — copying `/work/agent` gets you a dangling symlink, not the actual
files. Copy the real path instead.

## Lesson 10: not every failure is a pipeline bug

After fixing every Docker/network/YAML/memory issue above, `crs-claude-code`
still failed with `Claude Code exit code: 1`. The captured stdout (JSON
event stream) showed the actual cause plainly:

```json
{"type":"system","subtype":"api_retry","error_status":401,"error":"authentication_failed"}
{"type":"system","subtype":"api_retry","error_status":429,"error":"rate_limit"}
```

This was a genuinely invalid/exhausted Anthropic API key — confirmed
independently with a direct `curl` to `api.anthropic.com`, bypassing the
whole pipeline:

```bash
curl -s https://api.anthropic.com/v1/messages \
  -H "x-api-key: $ANTHROPIC_API_KEY" -H "anthropic-version: 2023-06-01" \
  -H "content-type: application/json" \
  -d '{"model":"claude-opus-4-8","max_tokens":10,"messages":[{"role":"user","content":"hi"}]}'
```

**Lesson:** when debugging a CI pipeline that calls out to a third-party
API, test the API credential directly and independently before assuming the
pipeline itself is broken. This one test would have saved significant time
if run earlier.

## Open item: GitLab "Suggested Changes" (Apply-button comments)

GitLab renders a one-click "Apply suggestion" button only for comments using
special inline syntax (` ```suggestion:-N+0 `) posted via the Discussions
API with structured diff position data, rather than the simpler Notes API
used for the general patch-preview comment (which works reliably).

Three structured attempts were made to compute the required `line_code`
field (`SHA1(file_path)_<line>_<line>`, using a running line-cursor for the
"missing" side of pure additions/removals — confirmed correct against a real
captured example) and to place it correctly in the request (as a top-level
field, then nested under `position.line_range.start/end`). All three
produced the identical GitLab error:
`Note {:line_code=>["can't be blank", "must be a valid line code"]}`.

This suggests either a further structural requirement not yet identified, or
that this GitLab version's frontend creates suggestions via a **GraphQL
mutation** rather than the REST Discussions API, meaning the REST payload
shape being guessed at may not be the right target at all. **Parked as a
known limitation** — the pipeline still posts the full patch (diff, preview,
browse link) as a regular comment, which is fully functional; only the
one-click apply button is missing. Worth revisiting with direct access to
GitLab's own frontend source or a packet capture, rather than DevTools
screenshots.

## Recommended approach for anyone attempting this

1. **Get `crs-scan` (bug-finding only) working first**, on simple push
   pipelines, before adding `crs-patch` (LLM-based). It has none of the
   LLM/litellm/API-key complexity and validates the DooD/networking/caching
   fixes in isolation.
2. **Reproduce failures locally (SSH) before debugging in CI.** Every hard
   bug in this document was ultimately diagnosed faster by running the exact
   same `oss-crs` commands directly on the server, with full interactive
   `docker exec`/`docker logs` access, than by iterating through CI log
   uploads.
3. **Size compose file resources to your actual hardware early** — don't
   wait for mysterious timeouts to reveal it.
4. **Test third-party API credentials independently** the moment something
   fails after making it deep into a pipeline — it's a five-second check
   that rules out an entire class of red herrings.

