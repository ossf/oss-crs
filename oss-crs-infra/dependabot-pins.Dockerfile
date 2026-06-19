# This file exists solely for Dependabot to track pinned image digests.
# Do NOT build this file. The actual references live in oss_crs/src/constants.py.
# When Dependabot opens a PR updating these, sync the SHAs back to constants.py.
# TODO: Explore Renovate for native regex-based tracking of image pins in Python files.
FROM ghcr.io/berriai/litellm-database@sha256:7a8cd7e903a9087808422bd9d6dc945a0ed719f6eac2a011bc3313d81db24b07  # v1.87.0
FROM postgres@sha256:8ff36f3c66371cba71d20ceedccfc3de9669a68737607888c4ef0af93abe8e39  # 18.4
