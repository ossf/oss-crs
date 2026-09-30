# This file exists solely for Dependabot to track pinned image digests.
# Do NOT build this file. The actual references live in oss_crs/src/constants.py.
# When Dependabot opens a PR updating these, sync the SHAs back to constants.py.
# TODO: Explore Renovate for native regex-based tracking of image pins in Python files.
# TODO: ALPINE_IMAGE and NIX_BUILDER_IMAGE in constants.py are digest-pinned but
# have no FROM line here, so nothing proposes updates for them and no advisory
# will ever fire against them. Add both once someone confirms a bump is safe:
# NIX_BUILDER_IMAGE feeds libCRS/deps.Dockerfile as a build-arg, and ALPINE_IMAGE
# is commented only "3.x latest", so its real version needs pinning down first.
FROM ghcr.io/berriai/litellm-database@sha256:70e3754699d2c5e969c65445e3b52cf58cf7998a815f2cb3551776152bbffaaf  # v1.101.0
FROM postgres@sha256:86c951e05bf56c93d95d397747fb8820ac76cc3bedb78f43abd83eedbe3666ae  # 18.6
