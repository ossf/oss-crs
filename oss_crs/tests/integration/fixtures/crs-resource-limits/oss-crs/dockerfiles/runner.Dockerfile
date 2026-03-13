FROM alpine:latest

# Install dependencies (libCRS not needed for this test fixture)
RUN apk add --no-cache bash python3

COPY bin/run_fuzzer.sh /usr/local/bin/run_fuzzer.sh
RUN chmod +x /usr/local/bin/run_fuzzer.sh

ENTRYPOINT ["/usr/local/bin/run_fuzzer.sh"]
