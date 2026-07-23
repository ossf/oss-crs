# SPDX-License-Identifier: MIT
# Container images used by the infrastructure sidecar stack.
LITELLM_IMAGE = "ghcr.io/berriai/litellm-database@sha256:64d3547e0b131bf4638342e52c12bc46d6f1d9b8498e4b731ff31be5ab316ea9"  # v1.92.0
POSTGRES_IMAGE = "postgres@sha256:b913fd5699b8bd23fa4b06d72ecdd939fad43b80fb8651bac06caa0e6d135cac"  # 18.4

# Stable local tags for the internal LiteLLM stack images. ``prepare`` pulls
# each by its immutable digest and applies the local tag; the run template
# references these local tags so offline runs resolve them locally.
OSS_CRS_LITELLM_TAG = "oss-crs-litellm:latest"
OSS_CRS_POSTGRES_TAG = "oss-crs-postgres:latest"

# Alpine image used by the cleanup helpers (``rm_with_docker`` and the
# ``oss-crs clean`` size fallback), which shell out to ``docker run --rm``
# to delete/measure root-owned files during run teardown and clean.
# ``prepare`` pulls this pinned digest once (unconditionally, since cleanup
# runs regardless of LLM mode) and tags it with ``OSS_CRS_ALPINE_TAG`` so
# offline teardown/clean resolve it locally instead of pulling per invocation.
ALPINE_IMAGE = "alpine@sha256:28bd5fe8b56d1bd048e5babf5b10710ebe0bae67db86916198a6eec434943f8b"  # 3.x latest
OSS_CRS_ALPINE_TAG = "oss-crs-alpine:latest"

# Pinned-digest images pulled (not built) for the internal LiteLLM stack, each
# mapped to a stable, infra-owned local tag. ``prepare`` pulls each by its
# immutable digest once (gated on internal-LLM mode) and then applies the local
# tag, so offline runs have them locally instead of pulling per run. Tagging is
# additive: the pulled image keeps its RepoDigest, so the digest references in
# the run template still resolve to the same local image, while the tag makes it
# visible in ``docker images`` and gives it a usable, stable handle.
OSS_CRS_INTERNAL_LLM_IMAGES = {
    LITELLM_IMAGE: OSS_CRS_LITELLM_TAG,
    POSTGRES_IMAGE: OSS_CRS_POSTGRES_TAG,
}

# Internal-LLM-only sidecars that are *built* from oss-crs-infra contexts (as
# opposed to the pinned images above, which are pulled). litellm-key-gen only
# runs when the LLM stack is in ``internal`` mode, so ``prepare`` builds it with
# its stable tag gated on that mode -- but the tag is always passed to the
# renderer so the compose template can reference it. Maps each context
# subdirectory under oss-crs-infra/ to its stable image tag.
OSS_CRS_INTERNAL_LLM_SIDECAR_IMAGES = {
    "litellm-key-gen": "oss-crs-litellm-key-gen:latest",
}

# Internal LiteLLM proxy URL exposed inside the Docker network.
LITELLM_INTERNAL_URL = "http://litellm.oss-crs:4000"

# Postgres defaults for the internal LiteLLM database.
POSTGRES_USER = "crs"
POSTGRES_PORT = 5432
POSTGRES_HOST = "postgres.oss-crs-infra-only"

# Docker repository name for preserved builder images (tagged copies of
# compose-built images kept for the sidecar and snapshot workflows).
PRESERVED_BUILDER_REPO = "oss-crs-builder"

# Sentinel used as the harness directory component when no --target-harness is
# specified (e.g. harness-gen CRSs that produce harnesses rather than consume them).
# OSS_CRS_TARGET_HARNESS is NOT set in the container environment in this case.
UNHARNESSED = "OSS_CRS_UNHARNESSED"

# Docker repository name for target-dependent run-phase images. These are
# produced by the build-target phase (tag embeds the target repo hash) and
# consumed unbuilt by the run phase, so they must survive run-time teardown
# sweeps just like preserved builder images.
PRESERVED_RUNNER_REPO = "oss-crs-runner"

# Infra sidecar images.
# These sidecars (exchange, lifecycle, builder/runner sidecars) are built from
# fixed oss-crs-infra contexts and take no target/CRS build args, so a single
# image serves every CRS module and every target. ``prepare`` builds them once
# with these stable, run-independent tags; the run phase reuses them instead of
# rebuilding per run (and offline runs rely on them already existing locally).
# Maps each context subdirectory under oss-crs-infra/ to its stable image tag.
OSS_CRS_INFRA_SIDECAR_IMAGES = {
    "exchange": "oss-crs-exchange:latest",
    "lifecycle": "oss-crs-lifecycle:latest",
    "builder-sidecar": "oss-crs-builder-sidecar:latest",
    "runner-sidecar": "oss-crs-runner-sidecar:latest",
}

# OSS-Fuzz base-runner image. CRS run-phase runners (that execute harness
# binaries) should start FROM this, tagged to match the OS the harness was
# built on, so the runtime glibc/ABI matches the build toolchain.
BASE_RUNNER_IMAGE = "gcr.io/oss-fuzz-base/base-runner"

# Sentinel project.yaml base_os_version meaning "unspecified": OSS-Fuzz maps it
# to the floating ":latest" runner tag. Mirrors infra/helper.py semantics.
LEGACY_BASE_OS_VERSION = "legacy"
DEFAULT_BASE_RUNNER_TAG = "latest"

# Pinned Nix builder image used to build the oss-crs-deps Docker image during
# prepare. Passed as the NIX_BUILDER_IMAGE build-arg to libCRS/deps.Dockerfile,
# where Nix runs inside an ephemeral Docker container; the host's own /nix is
# never touched or required.
NIX_BUILDER_IMAGE = "nixos/nix@sha256:e623d73af9cac82d1b50784c83e0cf2a4b83bfd2cfe8d5b67809a2fc94e043ac"  # v2.28.3

# The constant Docker image name for the Nix-built libCRS+rsync dependencies
# image. CRS builder Dockerfiles reference this via COPY --from=oss-crs-deps.
# Built during prepare via `docker build` (see oss_crs/src/libcrs_nix.py and
# libCRS/deps.Dockerfile) and tagged oss-crs-deps:latest.
OSS_CRS_DEPS_IMAGE = "oss-crs-deps"

# base_os_version values that map to a known base-runner OS tag. Unknown values
# are still passed through as the runner tag verbatim (OSS-Fuzz may add new OS
# lines over time), but warned about since a non-existent tag fails at pull.
KNOWN_BASE_OS_VERSIONS = frozenset(
    {LEGACY_BASE_OS_VERSION, "ubuntu-20-04", "ubuntu-24-04"}
)

# WebUI dashboard service (oss-crs-infra/webui). Default host port the service
# is served on and the docker container name used by the `crs_compose web-ui`
# command and by run-time metric publishing.
WEBUI_DEFAULT_PORT = 9090
WEBUI_CONTAINER_NAME = "oss-crs-webui"

# Synthetic CRS name for the best-effort coverage-instrumentation build that
# feeds the WebUI coverage panel (oss-crs-infra/webui). Not a real CRS — it
# reuses the CRS build/output plumbing under this name.
COVERAGE_CRS_NAME = "oss-crs-coverage"
