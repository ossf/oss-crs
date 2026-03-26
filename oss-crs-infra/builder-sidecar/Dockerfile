FROM python:3.12-slim

# Install dependencies
COPY requirements.txt /requirements.txt
RUN pip install --no-cache-dir -r /requirements.txt

# Copy application code
COPY server.py /server.py
COPY docker_ops.py /docker_ops.py
COPY oss_crs_handler.sh /oss_crs_handler.sh

# The Docker socket must be mounted at runtime:
#   -v /var/run/docker.sock:/var/run/docker.sock
# Run as root to access Docker socket (infra service, not user-facing)

EXPOSE 8080

CMD ["python3", "-u", "/server.py"]
