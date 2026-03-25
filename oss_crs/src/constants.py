# Container images used by the infrastructure sidecar stack.
LITELLM_IMAGE = "ghcr.io/berriai/litellm-database@sha256:7c51011535fc9347fc306c8ed7d2e4cdda2336ffd91d930a35fdccd034f2ec17"  # main-stable
POSTGRES_IMAGE = "postgres@sha256:20edbde7749f822887a1a022ad526fde0a47d6b2be9a8364433605cf65099416"  # 16-alpine

# Internal LiteLLM proxy URL exposed inside the Docker network.
LITELLM_INTERNAL_URL = "http://litellm.oss-crs:4000"

# Postgres defaults for the internal LiteLLM database.
POSTGRES_USER = "crs"
POSTGRES_PORT = 5432
POSTGRES_HOST = "postgres.oss-crs-infra-only"
