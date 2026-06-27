# Coverage Builder Dockerfile for OSS-CRS WebUI
# Compiles the target with SANITIZER=coverage and submits outputs.
#
# NOTE: intentionally runs as root (no USER instruction). This image is FROM the
# target's own base image and runs `compile`, which builds the target in-place
# and routinely writes root-owned files under /src, /work, etc.; dropping
# privileges would break the build for many targets. The Kusari "container as
# root" finding does not apply under our threat model: this container takes no
# untrusted network input — its only inputs are the target source and the CLI
# flags supplied by the operator running the build, so there is no
# attacker-controlled path to code execution here.

ARG target_base_image
ARG crs_version

FROM ${target_base_image}

# Install libCRS
COPY --from=libcrs . /libCRS
RUN /libCRS/install.sh

COPY bin/compile_target_coverage /usr/local/bin/compile_target_coverage

CMD ["compile_target_coverage"]
