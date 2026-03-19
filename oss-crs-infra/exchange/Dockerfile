ARG target_base_image=python:3.12-slim
FROM ${target_base_image}

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-pip python3-venv rsync \
    && rm -rf /var/lib/apt/lists/*

# Install seed ensembler package
COPY seed-ensembler /opt/seed-ensembler
RUN pip3 install --break-system-packages /opt/seed-ensembler

# Keep original passthrough-only main.py as reference
COPY exchange/main.py /main.py

CMD ["python3", "-u", "-m", "seed_ensembler"]
