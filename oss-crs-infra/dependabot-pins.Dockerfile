# This file exists solely for Dependabot to track pinned image digests.
# Do NOT build this file. The actual references live in oss_crs/src/constants.py.
# When Dependabot opens a PR updating these, sync the SHAs back to constants.py.
# TODO: Explore Renovate for native regex-based tracking of image pins in Python files.
# TODO: ALPINE_IMAGE and NIX_BUILDER_IMAGE in constants.py are digest-pinned but
# have no FROM line here, so nothing proposes updates for them and no advisory
# will ever fire against them. Add both once someone confirms a bump is safe:
# NIX_BUILDER_IMAGE feeds libCRS/deps.Dockerfile as a build-arg, and ALPINE_IMAGE
# is commented only "3.x latest", so its real version needs pinning down first.
FROM ghcr.io/berriai/litellm-database@sha256:7ef0e78fe00ad5ea7ea45666cc1d1f02cb7076aee69091543bf397de52db33ce  # v1.106.4
FROM postgres@sha256:edb0c08dc33befd0262ab3b5cf1c4f3f8cfd8b09e88c05b45adee29d6761ddb6  # 18.6
