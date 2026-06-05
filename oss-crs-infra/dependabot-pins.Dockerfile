# This file exists solely for Dependabot to track pinned image digests.
# Do NOT build this file. The actual references live in oss_crs/src/constants.py.
# When Dependabot opens a PR updating these, sync the SHAs back to constants.py.
# TODO: Explore Renovate for native regex-based tracking of image pins in Python files.
FROM ghcr.io/berriai/litellm-database@sha256:23f40f60a9effdc064ca6affe1d9f6f4f976604feab0682f0401a2046af54b37  # v1.87.0
FROM postgres@sha256:8ff36f3c66371cba71d20ceedccfc3de9669a68737607888c4ef0af93abe8e39  # 18.4
