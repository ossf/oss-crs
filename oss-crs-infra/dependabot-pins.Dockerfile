# This file exists solely for Dependabot to track pinned image digests.
# Do NOT build this file. The actual references live in oss_crs/src/constants.py.
# When Dependabot opens a PR updating these, sync the SHAs back to constants.py.
# TODO: Explore Renovate for native regex-based tracking of image pins in Python files.
# TODO: ALPINE_IMAGE and NIX_BUILDER_IMAGE in constants.py are digest-pinned but
# have no FROM line here, so nothing proposes updates for them and no advisory
# will ever fire against them. Add both once someone confirms a bump is safe:
# NIX_BUILDER_IMAGE feeds libCRS/deps.Dockerfile as a build-arg, and ALPINE_IMAGE
# is commented only "3.x latest", so its real version needs pinning down first.
FROM ghcr.io/berriai/litellm-database@sha256:2bcadb9905b692a5ca4dadd51bccaaa23a95dc8a0f9322d9c9787f0ac2aeb50e  # v1.104.2
FROM postgres@sha256:74935e72241653ca55e0414067e6d8763aceb8a810eb51b452253ec3dcfc4336  # 18.6
