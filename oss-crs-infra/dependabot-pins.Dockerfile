# This file exists solely for Dependabot to track pinned image digests.
# Do NOT build this file. The actual references live in oss_crs/src/constants.py.
# When Dependabot opens a PR updating these, sync the SHAs back to constants.py.
# TODO: Explore Renovate for native regex-based tracking of image pins in Python files.
# Mapping to constants.py: litellm-database -> LITELLM_IMAGE, postgres ->
# POSTGRES_IMAGE, alpine -> ALPINE_IMAGE, nixos/nix -> NIX_BUILDER_IMAGE (also
# the default ARG in libCRS/deps.Dockerfile).
FROM ghcr.io/berriai/litellm-database@sha256:932c54440654f8f8466a85887612d95dcfb04ae2fc01257844332f4bf3b96986  # v1.109.1
FROM postgres@sha256:2ea8630df176ad090ec92136dee850397c5af8781e4bcf2e4162593691c61bbc  # 18.7
FROM alpine@sha256:f59abe656b692d901e184be3445e084786602108599d2a51d229c0f5a48c71bf  # 3.24.4
FROM nixos/nix@sha256:b49cb33f3a7e5cf3933ad8774529de0e3807ec5cf54987aa7ccdfb49f2fc55ed  # 2.35.6
