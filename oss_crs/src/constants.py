# SPDX-License-Identifier: MIT
# Container images used by the infrastructure sidecar stack.
LITELLM_IMAGE = "ghcr.io/berriai/litellm-database@sha256:2dec2d0228b7ad35126e3be5eb8969d8151563dfe5c79a3f25e6ebbd28bf607b"  # main-v1.83.10-stable
POSTGRES_IMAGE = "postgres@sha256:20edbde7749f822887a1a022ad526fde0a47d6b2be9a8364433605cf65099416"  # 16-alpine

# Internal LiteLLM proxy URL exposed inside the Docker network.
LITELLM_INTERNAL_URL = "http://litellm.oss-crs:4000"

# Postgres defaults for the internal LiteLLM database.
POSTGRES_USER = "crs"
POSTGRES_PORT = 5432
POSTGRES_HOST = "postgres.oss-crs-infra-only"

# Docker repository name for preserved builder images (tagged copies of
# compose-built images kept for the sidecar and snapshot workflows).
PRESERVED_BUILDER_REPO = "oss-crs-builder"
