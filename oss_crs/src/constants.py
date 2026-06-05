# SPDX-License-Identifier: MIT
# Container images used by the infrastructure sidecar stack.
LITELLM_IMAGE = "ghcr.io/berriai/litellm-database@sha256:23f40f60a9effdc064ca6affe1d9f6f4f976604feab0682f0401a2046af54b37"  # v1.87.0
POSTGRES_IMAGE = "postgres@sha256:8ff36f3c66371cba71d20ceedccfc3de9669a68737607888c4ef0af93abe8e39"  # 18.4

# Internal LiteLLM proxy URL exposed inside the Docker network.
LITELLM_INTERNAL_URL = "http://litellm.oss-crs:4000"

# Postgres defaults for the internal LiteLLM database.
POSTGRES_USER = "crs"
POSTGRES_PORT = 5432
POSTGRES_HOST = "postgres.oss-crs-infra-only"

# Docker repository name for preserved builder images (tagged copies of
# compose-built images kept for the sidecar and snapshot workflows).
PRESERVED_BUILDER_REPO = "oss-crs-builder"
