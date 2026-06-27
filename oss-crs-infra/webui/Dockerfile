FROM python:3.12-slim

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app/

# Drop root (defense in depth). The service only reads /app and writes to the
# bind-mounted /webui_logs. The launcher (`oss-crs web-ui` / `run --web-ui`)
# additionally passes `--user <host-uid>:<host-gid>` at runtime so writes to
# that host-owned directory succeed regardless of this image's default UID.
RUN useradd --create-home --uid 10001 webui
USER webui

CMD ["python3", "-u", "/app/main.py"]
