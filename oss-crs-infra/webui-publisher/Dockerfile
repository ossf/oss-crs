FROM python:3.12-slim

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app/

# Drop root (defense in depth). The sidecar reads its ro mounts, writes only to
# /tmp (tempfile) and the bind-mounted /webui_logs. The run compose pins this
# service to the host UID/GID (see `user:` in the generated compose) so writes
# to that host-owned directory succeed regardless of this image's default UID.
RUN useradd --create-home --uid 10001 publisher
USER publisher

CMD ["python3", "-u", "/app/main.py"]
