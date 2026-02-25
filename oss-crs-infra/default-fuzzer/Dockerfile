# Default fuzzer sidecar - uses base-runner image with compiled /out/ binaries
FROM gcr.io/oss-fuzz-base/base-runner

# Install dependencies for fuzzer server
RUN pip3 install fastapi uvicorn

# Copy fuzzer server from templates context
COPY --from=oss_crs_templates oss_crs_fuzzer_server.py /usr/local/bin/oss_crs_fuzzer_server.py

CMD ["python3", "/usr/local/bin/oss_crs_fuzzer_server.py"]
