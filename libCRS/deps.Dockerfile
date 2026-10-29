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
# It also ships /wheelhouse (see below) for installing the libCRS Python API
# offline into a CRS's own interpreter:
#
#   COPY --from=oss-crs-deps /wheelhouse /tmp/libcrs-wheelhouse
#   RUN pip install --no-index --find-links /tmp/libcrs-wheelhouse libcrs
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

# Export /wheelhouse: the libCRS wheel plus its dependencies at the versions
# pinned in uv.lock (hash-checked). Only interpreter-independent wheels
# (py3-none-any, or py3-none-manylinux per architecture) are fetched, so a CRS
# can `pip install --no-index --find-links` libCRS into any Python >= 3.10.
RUN nix shell /build#wheelhouse-tools \
      --extra-experimental-features 'nix-command flakes' \
      --command sh -euc ' \
        cp -R /build /tmp/libcrs-src; \
        uv export --project /tmp/libcrs-src --frozen --no-dev --no-emit-project \
          --format requirements-txt --output-file /tmp/requirements.txt; \
        for arch in x86_64 aarch64; do \
          pip download --no-deps --only-binary=:all: \
            --implementation py --abi none --python-version 3.10 \
            --platform "manylinux2014_${arch}" --platform any \
            --requirement /tmp/requirements.txt --dest /rootfs/wheelhouse; \
        done; \
        pip wheel --no-deps --no-build-isolation \
          --wheel-dir /rootfs/wheelhouse /tmp/libcrs-src'

FROM scratch
COPY --from=build /rootfs/ /
# Lets `oss-crs prepare` tell this image apart from older ones built before
# /wheelhouse existed, so those are rebuilt instead of reused.
LABEL org.ossf.oss-crs.deps.wheelhouse="1"
