# This file exists solely for Dependabot to track pinned image digests.
# Do NOT build this file. The actual references live in oss_crs/src/constants.py.
# When Dependabot opens a PR updating these, sync the SHAs back to constants.py.
# TODO: Explore Renovate for native regex-based tracking of image pins in Python files.
# TODO: ALPINE_IMAGE and NIX_BUILDER_IMAGE in constants.py are digest-pinned but
# have no FROM line here, so nothing proposes updates for them and no advisory
# will ever fire against them. Add both once someone confirms a bump is safe:
# NIX_BUILDER_IMAGE feeds libCRS/deps.Dockerfile as a build-arg, and ALPINE_IMAGE
# is commented only "3.x latest", so its real version needs pinning down first.
FROM ghcr.io/berriai/litellm-database@sha256:5a9ff0fd7177372f2ccd8949cb121f67e281f4d13d026af51f9c096c0626f50a  # v1.104.0
FROM postgres@sha256:5a5a84b19854a9ffaa54082c166ff4ec27473a361e496e5ea167f298f2da9722  # 18.6
