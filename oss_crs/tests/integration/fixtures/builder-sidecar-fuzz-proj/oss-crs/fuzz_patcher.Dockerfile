ARG target_base_image
ARG crs_version

FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y python3 python3-pip curl && rm -rf /var/lib/apt/lists/*

# Install libCRS (CLI + Python package)
COPY --from=libcrs . /libCRS
RUN /libCRS/install.sh

COPY bin/apply_fuzz_proj_patch /usr/local/bin/apply_fuzz_proj_patch
COPY bin/fuzz_proj_patch.diff /usr/local/bin/fuzz_proj_patch.diff
COPY bin/target_source_patch.diff /usr/local/bin/target_source_patch.diff
RUN chmod +x /usr/local/bin/apply_fuzz_proj_patch

CMD ["apply_fuzz_proj_patch"]
