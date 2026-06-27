# This file exists solely for Dependabot to track pinned image digests.
# Do NOT build this file. The actual references live in oss_crs/src/constants.py.
# When Dependabot opens a PR updating these, sync the SHAs back to constants.py.
# TODO: Explore Renovate for native regex-based tracking of image pins in Python files.
FROM ghcr.io/berriai/litellm-database@sha256:b398150d008dd36a57fa094314b6cd428d832c5264094fd3cd455b93d640ab11  # v1.89.3
FROM postgres@sha256:29ee7bb30d804447dc9a91fd0d74322ae1dc3a4072cc6346f70a5ed6e783b565  # 18.4
