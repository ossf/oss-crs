# syntax=docker/dockerfile:1
#
# Builds the "oss-crs-deps" image (libCRS + rsync) directly via `docker build`.
#
# The build stage runs Nix inside a pinned nixos/nix container to produce the
# `libcrs-runtime` closure (Python with libCRS installed, plus rsync), then
# assembles a minimal rootfs containing:
#
#   /nix/store/<runtime closure>          (only runtime paths, not build deps)
#   /usr/local/bin/libCRS  -> /nix/store/<hash>-libcrs-runtime/bin/libCRS
#   /usr/local/bin/rsync   -> /nix/store/<hash>-libcrs-runtime/bin/rsync
#
# The final `FROM scratch` stage copies that rootfs, so CRS builder Dockerfiles
# can do:
#
#   COPY --from=oss-crs-deps /nix/store /nix/store
#   COPY --from=oss-crs-deps /usr/local/bin/libCRS /usr/local/bin/libCRS
#   COPY --from=oss-crs-deps /usr/local/bin/rsync  /usr/local/bin/rsync
#
# The image is never run (no shell/CMD); it exists purely as a COPY --from source.
ARG NIX_BUILDER_IMAGE=nixos/nix:2.28.3
FROM ${NIX_BUILDER_IMAGE} AS build

# The libCRS directory (containing flake.nix) is the build context.
COPY . /build

# Build the runtime closure and stage only its transitive runtime paths into
# /rootfs. `nix-store -qR` yields the exact runtime closure (equivalent to what
# dockerTools.buildLayeredImage would ship), excluding build-time dependencies.
RUN nix build /build#libcrs-runtime -o /out/runtime \
      --extra-experimental-features 'nix-command flakes' \
 && mkdir -p /rootfs/nix/store /rootfs/usr/local/bin \
 && for p in $(nix-store -qR /out/runtime); do cp -a "$p" /rootfs/nix/store/; done \
 && ln -s "$(readlink -f /out/runtime)/bin/libCRS" /rootfs/usr/local/bin/libCRS \
 && ln -s "$(readlink -f /out/runtime)/bin/rsync"  /rootfs/usr/local/bin/rsync

FROM scratch
COPY --from=build /rootfs/ /
