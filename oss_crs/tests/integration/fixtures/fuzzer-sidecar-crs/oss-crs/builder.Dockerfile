# Builder for fuzzer-sidecar-crs - compiles target and outputs to /out
ARG target_base_image
FROM ${target_base_image}

# Install libCRS
COPY --from=libcrs . /tmp/libCRS
RUN pip3 install /tmp/libCRS

# Copy compile script
COPY oss-crs/compile_target.sh /compile_target.sh
RUN chmod +x /compile_target.sh

CMD ["/compile_target.sh"]
