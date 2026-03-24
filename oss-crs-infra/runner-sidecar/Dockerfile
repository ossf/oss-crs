FROM gcr.io/oss-fuzz-base/base-runner

# Install Python and pip for the FastAPI HTTP server.
# base-runner may already have Python3 but pip is not guaranteed.
RUN apt-get update && apt-get install -y python3 python3-pip && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /requirements.txt
RUN pip3 install --no-cache-dir -r /requirements.txt

COPY server.py /server.py

EXPOSE 8080
CMD ["python3", "-u", "/server.py"]
