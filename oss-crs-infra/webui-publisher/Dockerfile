# Built FROM the OSS-Fuzz base-runner (tagged to the target's base_os_version,
# passed in as base_runner_image) rather than a generic python image. The
# publisher executes the target's coverage-instrumented binaries to collect
# coverage, so it needs the SAME runtime libraries those binaries were linked
# against (e.g. libssl.so.1.1 on Ubuntu 20.04 targets). base-runner is the
# runtime counterpart of the base-builder the binaries were compiled in, and
# also bundles the llvm coverage tools (llvm-profdata/llvm-cov).
ARG base_runner_image
FROM ${base_runner_image}

COPY requirements.txt /app/requirements.txt
RUN pip3 install --no-cache-dir -r /app/requirements.txt

COPY . /app/

# Drop root (defense in depth). The sidecar reads its ro mounts, writes only to
# /tmp (tempfile) and the bind-mounted /webui_logs. The run compose pins this
# service to the host UID/GID (see `user:` in the generated compose) so writes
# to that host-owned directory succeed regardless of this image's default UID.
RUN useradd --create-home --uid 10001 publisher
USER publisher

CMD ["python3", "-u", "/app/main.py"]
