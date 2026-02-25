# Agent container for fuzzer-sidecar-crs
# Uses base-runner which has access to libCRS
ARG target_base_image
FROM ${target_base_image}

# Install libCRS as a Python package (not just CLI tool)
# The libcrs context is mounted by docker-compose
COPY --from=libcrs . /tmp/libCRS
RUN pip3 install /tmp/libCRS

# Copy the agent script (relative to context which is the CRS root)
COPY oss-crs/run.py /run.py

# Run the fuzzing agent
CMD ["python3", "/run.py"]
