FROM python:3.12-slim

# git: apply fuzz-proj patches. buildx: rebuild the CRS builder image on top of the
# patched project image (needs BuildKit --build-context, which the docker-py SDK
# cannot supply). The buildx binary runs standalone (it talks to the host daemon
# over the mounted socket), so we don't need the docker CLI or daemon here — just
# the plugin binary placed on PATH as `docker-buildx`. Fetched from its official
# release since it is not in every Debian suite's apt repo.
ARG BUILDX_VERSION=v0.19.3
RUN apt-get update \
    && apt-get install -y --no-install-recommends git curl ca-certificates \
    && curl -fsSL \
        "https://github.com/docker/buildx/releases/download/${BUILDX_VERSION}/buildx-${BUILDX_VERSION}.linux-amd64" \
        -o /usr/local/bin/docker-buildx \
    && chmod +x /usr/local/bin/docker-buildx \
    && apt-get purge -y curl && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies
COPY requirements.txt /requirements.txt
RUN pip install --no-cache-dir -r /requirements.txt

# Copy application code
COPY server.py /server.py
COPY docker_ops.py /docker_ops.py
COPY oss_crs_handler.sh /oss_crs_handler.sh
COPY run_tests.sh /run_tests.sh

# The Docker socket must be mounted at runtime:
#   -v /var/run/docker.sock:/var/run/docker.sock
# Run as root to access Docker socket (infra service, not user-facing)

EXPOSE 8080

CMD ["python3", "-u", "/server.py"]
