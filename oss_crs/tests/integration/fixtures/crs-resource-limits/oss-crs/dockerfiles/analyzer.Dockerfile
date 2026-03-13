FROM alpine:latest

# Install dependencies (libCRS not needed for this test fixture)
RUN apk add --no-cache bash python3

COPY bin/run_analyzer.sh /usr/local/bin/run_analyzer.sh
RUN chmod +x /usr/local/bin/run_analyzer.sh

ENTRYPOINT ["/usr/local/bin/run_analyzer.sh"]
