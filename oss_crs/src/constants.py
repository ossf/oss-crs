# SPDX-License-Identifier: MIT
# Container images used by the infrastructure sidecar stack.
LITELLM_IMAGE = "ghcr.io/berriai/litellm-database@sha256:64d3547e0b131bf4638342e52c12bc46d6f1d9b8498e4b731ff31be5ab316ea9"  # v1.92.0
POSTGRES_IMAGE = "postgres@sha256:b913fd5699b8bd23fa4b06d72ecdd939fad43b80fb8651bac06caa0e6d135cac"  # 18.4

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
