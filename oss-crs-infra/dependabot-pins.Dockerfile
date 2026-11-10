# This file exists solely for Dependabot to track pinned image digests.
# Do NOT build this file. The actual references live in oss_crs/src/constants.py.
# When Dependabot opens a PR updating these, sync the SHAs back to constants.py.
# TODO: Explore Renovate for native regex-based tracking of image pins in Python files.
# TODO: ALPINE_IMAGE and NIX_BUILDER_IMAGE in constants.py are digest-pinned but
# have no FROM line here, so nothing proposes updates for them and no advisory
# will ever fire against them. Add both once someone confirms a bump is safe:
# NIX_BUILDER_IMAGE feeds libCRS/deps.Dockerfile as a build-arg, and ALPINE_IMAGE
# is commented only "3.x latest", so its real version needs pinning down first.
FROM ghcr.io/berriai/litellm-database@sha256:80c91fd00cc039e8880474f539544b919f14cd258a22f22ce4218b864c4ff14a  # v1.106.4
FROM postgres@sha256:e3d24f6906d0760bf4da0dae872415391ededefad23fd471c47dc53b0ddf3c42  # 18.6
